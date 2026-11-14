"""Spoken summaries: a short, optional status line, never the full reply."""
from __future__ import annotations

import re

SYSTEM = (
    "You write a very short spoken status update for a voice assistant. "
    "Say what was done or what the user needs to know, in at most 25 words, "
    "in one or two plain sentences, first person ('I ...'), no markdown, no URLs, "
    "no IDs, no lists. Mention a number only if it matters. "
    "If there is nothing worth saying aloud, reply with exactly: NONE"
)

_URL = re.compile(r"https?://\S+")
_MD = re.compile(r"[*_`#>\[\]]")


def clean_for_speech(text: str, max_words: int = 30) -> str | None:
    text = (text or "").strip().strip('"')
    if not text or text.upper().startswith("NONE"):
        return None
    text = _URL.sub("", text)
    text = _MD.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split(" ")
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(",;:") + "."
    return text or None


def should_speak(rules: dict, *, used_tools: bool, had_approval: bool, had_error: bool, seconds: float) -> bool:
    """Decide whether a finished run deserves a spoken summary."""
    return bool(
        (rules.get("tools", True) and used_tools)
        or (rules.get("approvals", True) and had_approval)
        or (rules.get("errors", True) and had_error)
        or seconds >= float(rules.get("min_seconds", 10))
    )


async def summarize(provider, model: str | None, request: str, actions: list[str], reply: str) -> str | None:
    acts = "\n".join(f"- {a}" for a in actions) or "- (no actions)"
    user = f"User request: {request[:500]}\nActions:\n{acts}\nFinal reply: {reply[:1200]}"
    out = await provider.complete(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], model=model or None
    )
    return clean_for_speech(out)
