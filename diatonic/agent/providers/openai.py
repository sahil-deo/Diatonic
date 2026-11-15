"""Any OpenAI-compatible chat API: Google AI Studio (Gemini), Groq, OpenRouter, LM Studio, OpenAI itself...

Produces the same chunks as the Ollama provider, so the agent loop doesn't care which one is in use. The loop's
messages are in Ollama's shape (tool calls without ids, arguments as objects); they are converted here.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx

from .base import Provider, ProviderError


# Gemini's thinking models sign each tool call and want the signature back with it. Calls that have none (replayed
# from an earlier turn, or made by another model) carry Google's documented placeholder instead.
GEMINI_NO_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def to_openai(messages: list[dict], gemini: bool = False) -> list[dict]:
    """Give tool calls ids and JSON-string arguments, and link each tool result to its call."""
    out, pending, n = [], [], 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function") or c
                n += 1
                cid = f"call_{n}"
                pending.append(cid)
                args = fn.get("arguments", {})
                call = {"id": cid, "type": "function", "function": {"name": fn.get("name", ""),
                        "arguments": args if isinstance(args, str) else json.dumps(args)}}
                extra = c.get("extra") or (GEMINI_NO_SIGNATURE if gemini else None)
                if extra:
                    call["extra_content"] = extra
                calls.append(call)
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif m.get("role") == "tool":
            cid = pending.pop(0) if pending else f"call_{n}"
            out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content") or ""})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


class _Think:
    """Drops <think>...</think> reasoning that some hosted models (Qwen, DeepSeek) put in the reply text."""

    def __init__(self):
        self.inside, self.buf = False, ""

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            if self.inside:
                end = self.buf.find("</think>")
                if end < 0:
                    self.buf = self.buf[-7:]       # a closing tag may be split across chunks
                    return out
                self.buf, self.inside = self.buf[end + 8:].lstrip(), False
                continue
            start = self.buf.find("<think>")
            if start >= 0:
                out, self.buf, self.inside = out + self.buf[:start], self.buf[start + 7:], True
                continue
            hold = next((i for i in range(min(6, len(self.buf)), 0, -1) if "<think>".startswith(self.buf[-i:])), 0)
            out, self.buf = out + self.buf[:len(self.buf) - hold], self.buf[len(self.buf) - hold:]
            return out

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        return rest


class OpenAICompatProvider(Provider):
    @property
    def base(self) -> str:
        return self.cfg["endpoint"].rstrip("/")

    @property
    def host(self) -> str:
        return urlparse(self.base).hostname or self.base

    def _headers(self) -> dict:
        key = self.cfg.get("api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _error(self, status: int, detail: str) -> ProviderError:
        if status in (401, 403):
            return ProviderError(f"{self.host} rejected the API key. Check it in Settings → Models.")
        if status == 404:
            return ProviderError(f"{self.host} doesn't know the model '{self.cfg['model']}'. Check its name in Settings → Models.")
        if status == 429:
            return ProviderError(f"{self.host} is rate-limiting requests (429). Wait a moment, or switch models.")
        return ProviderError(f"{self.host} error {status}: {detail[:300]}")

    async def stream(self, messages, tools=None, model=None, options=None) -> AsyncIterator[dict]:
        c = self.cfg
        body = {"model": model or c["model"], "messages": to_openai(messages, gemini=self.host.endswith("googleapis.com")), "stream": True,
                "temperature": (options or {}).get("temperature", c.get("temperature", 0.6)),
                "max_tokens": (options or {}).get("num_predict", c.get("max_tokens", 1024)),
                "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = tools
        if not self.cfg.get("api_key"):
            raise ProviderError(f"No API key for {self.host}. Add it in Settings → Models.")
        first_t, idle_t = float(c.get("first_token_timeout", 240)), float(c.get("idle_timeout", 60))
        t0 = time.monotonic()
        for attempt in range(4):
            try:
                async with self.http.stream("POST", f"{self.base}/chat/completions", json=body, headers=self._headers(),
                                            timeout=httpx.Timeout(30.0, read=first_t)) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode(errors="replace")
                        if r.status_code == 400 and "stream_options" in detail and "stream_options" in body:
                            body.pop("stream_options")      # some compatible APIs don't take it
                            continue
                        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                            await asyncio.sleep(min(8, 1.5 * 2 ** attempt))
                            continue
                        raise self._error(r.status_code, detail)
                    async for chunk in self._read(r, t0, first_t, idle_t):
                        yield chunk
                    return
            except httpx.ConnectError:
                raise ProviderError(f"Can't reach {self.host}. Check your internet connection and the endpoint in Settings → Models.")
            except httpx.ReadTimeout:
                raise ProviderError(f"{self.host} took too long to respond.")

    async def _read(self, r, t0: float, first_t: float, idle_t: float) -> AsyncIterator[dict]:
        calls: dict[int, dict] = {}
        usage, finish, ttft, think = None, None, None, _Think()
        lines = r.aiter_lines().__aiter__()
        while True:
            try:
                line = await asyncio.wait_for(lines.__anext__(), first_t if ttft is None else idle_t)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise ProviderError(f"The model stopped responding (no output for {int(first_t if ttft is None else idle_t)}s).")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get("error"):
                raise ProviderError(f"{self.host}: {ev['error'].get('message') if isinstance(ev['error'], dict) else ev['error']}")
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    ttft = ttft if ttft is not None else time.monotonic()
                    text = think.feed(d["content"])
                    if text:
                        yield {"content": text}
                for tc in d.get("tool_calls") or []:
                    ttft = ttft if ttft is not None else time.monotonic()
                    slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": "", "extra": None})
                    if tc.get("extra_content"):   # Gemini: the call's thought signature, which must go back with it
                        slot["extra"] = tc["extra_content"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    a = fn.get("arguments")
                    slot["arguments"] += a if isinstance(a, str) else json.dumps(a) if a else ""
                finish = ch.get("finish_reason") or finish
        rest = think.flush()
        if rest:
            yield {"content": rest}
        if calls:
            out = []
            for _, c in sorted(calls.items()):
                try:
                    args = json.loads(c["arguments"] or "{}")
                except ValueError:
                    args = c["arguments"]          # the loop forgives a malformed string
                out.append({"name": c["name"], "arguments": args, **({"extra": c["extra"]} if c["extra"] else {})})
            yield {"tool_calls": out}
        end = time.monotonic()
        u = usage or {}
        yield {"usage": {"prompt": u.get("prompt_tokens", 0), "completion": u.get("completion_tokens", 0),
                         "prefill_ms": int(((ttft or end) - t0) * 1000), "gen_ms": int((end - (ttft or end)) * 1000), "load_ms": 0},
               "truncated": finish == "length"}

    async def list_models(self) -> list[str]:
        r = await self.http.get(f"{self.base}/models", headers=self._headers(), timeout=10)
        if r.status_code != 200:
            raise self._error(r.status_code, r.text)
        return sorted(str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", []) if m.get("id"))

    async def health(self) -> dict:
        if not self.cfg.get("api_key"):
            return {"ok": False, "model_present": False, "models": [], "error": f"No API key for {self.host}. Add it in Settings → Models."}
        try:
            names = await self.list_models()
        except ProviderError as e:
            return {"ok": False, "model_present": False, "models": [], "error": str(e)}
        except Exception as e:
            return {"ok": False, "model_present": False, "models": [], "error": f"Can't reach {self.host} ({type(e).__name__})."}
        want = self.cfg["model"].removeprefix("models/")
        present = not names or want in names     # some APIs list only part of what they serve
        return {"ok": True, "model_present": present, "models": names,
                "error": None if present else f"{self.host} doesn't list '{want}'. Check the model name in Settings → Models."}
