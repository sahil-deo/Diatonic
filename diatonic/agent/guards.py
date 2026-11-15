"""Guardrails for small, loop-prone models.

- detect_loop / collapse_repeats: find and remove degenerate repetition in generated text.
- RunGuard: per-run budgets (time, tool calls), duplicate-call memory, failure counting.
"""
from __future__ import annotations

import json
import re
import time

_SPAN = re.compile(r"[^.!?\n]+[.!?]*")
_NORM = re.compile(r"\W+")
MIN_SENTENCE = 25   # normalised characters; shorter sentences ("Done.") may legitimately repeat
LONG_SENTENCE = 60  # a long sentence repeated even once is almost surely a loop


def _norm(s: str) -> str:
    return _NORM.sub(" ", s.lower()).strip()


def detect_loop(text: str) -> int | None:
    """Return the index to truncate at if `text` has degenerated into repetition, else None.

    The text before the index contains the first copy of whatever was repeated.
    """
    n = len(text)
    if n < 60:
        return None

    # 1) a short unit repeating back-to-back at the tail ("... ... ...", "the the the", "- - -")
    tail = text[-800:]
    off = n - len(tail)
    for unit_len in range(1, 161):
        if unit_len * 4 > len(tail):
            break
        unit = tail[-unit_len:]
        if not unit.strip():
            continue
        reps, pos = 1, len(tail) - unit_len
        while pos - unit_len >= 0 and tail[pos - unit_len:pos] == unit:
            reps += 1
            pos -= unit_len
        if reps >= 4 and reps * unit_len >= 40:
            return off + pos + unit_len

    # 2) the same sentence again and again (possibly interleaved with others)
    complete_end = n if text.rstrip()[-1:] in ".!?\n" or text.endswith("\n") else None
    seen: dict[str, list[int]] = {}
    for m in _SPAN.finditer(text):
        s = m.group().strip()
        key = _norm(s)
        if len(key) < MIN_SENTENCE:
            continue
        if m.end() == n and complete_end is None:  # still being written
            continue
        starts = seen.setdefault(key, [])
        starts.append(m.start())
        if len(starts) >= 3 or (len(key) >= LONG_SENTENCE and len(starts) >= 2):
            return starts[1] if len(starts) >= 2 else starts[0]
    return None


def collapse_repeats(text: str) -> str:
    """Remove repeated sentences/paragraphs (keeps the first copy). Safe to run on stored history."""
    if not text or "```" in text:
        return text
    cut = detect_loop(text)
    while cut is not None and cut > 0:
        text = text[:cut].rstrip()
        cut = detect_loop(text)
    seen: set[str] = set()
    out_paras = []
    for para in re.split(r"\n{2,}", text):
        kept = []
        for m in _SPAN.finditer(para):
            s = m.group().strip()
            key = _norm(s)
            if len(key) >= MIN_SENTENCE:
                if key in seen:
                    continue
                seen.add(key)
            kept.append(s)
        if kept:
            out_paras.append(" ".join(kept) if "\n" not in para else para)
    return "\n\n".join(out_paras).strip()


_INTENT = re.compile(r"\b(let me(?! know)|i'll|i will|i am going to|i'm going to|first, i|now i)\b", re.I)
_ACTION = re.compile(r"\b(check|list|look|fetch|get|search|find|verify|see|retrieve|pull|open|update|create)\b", re.I)


def looks_unfinished(text: str) -> bool:
    """True if the reply announces an action it never took ("Let me check…")."""
    t = text.strip()
    if not t:
        return False
    last = re.split(r"(?<=[.!?])\s+|\n+", t)[-1]
    return bool(_INTENT.search(last) and _ACTION.search(last)) or t.endswith(":")


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rstrip() + f"… [{len(s) - n} more characters]"


class RunGuard:
    """Budgets and memory for a single run."""

    def __init__(self, cfg: dict, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + float(cfg.get("run_timeout", 300))
        self.max_calls = int(cfg.get("max_tool_calls", 10))
        self.calls = self.dups = self.errors = self.loops = 0
        self.nudged = False
        self.approval_timeout: float | None = None
        self.readonly = False
        self.cloud, self.grants, self.model_label, self.conv_id = False, set(), "", None   # set per run: cloud model and what the user allowed it   # unattended run: tools that change things are never offered or auto-added
        self.seen: dict[str, dict] = {}

    def expired(self) -> bool:
        return self.clock() > self.deadline

    @staticmethod
    def key(name: str, args: dict) -> str:
        return name + json.dumps(args, sort_keys=True, default=str)

    def remember(self, key: str, *, ok: bool, content: str, risk: str) -> None:
        if ok and risk != "read":  # a successful change makes earlier reads stale
            self.seen = {k: v for k, v in self.seen.items() if v["risk"] != "read"}
        self.seen[key] = {"ok": ok, "content": content, "risk": risk}

    @property
    def must_finalize(self) -> bool:
        return self.dups >= 2 or self.errors >= 3 or self.calls >= self.max_calls
