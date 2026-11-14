from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

from .base import Provider, ProviderError


class OllamaProvider(Provider):
    @property
    def base(self) -> str:
        return self.cfg["endpoint"].rstrip("/")

    def _options(self, extra: dict | None) -> dict:
        c = self.cfg
        opts = {"temperature": c["temperature"], "top_p": c.get("top_p", 0.8), "top_k": c.get("top_k", 20),
                "repeat_penalty": c.get("repeat_penalty", 1.05), "num_ctx": c["num_ctx"],
                "num_predict": c.get("max_tokens", 1024)}
        return {**opts, **(extra or {})}

    async def stream(self, messages, tools=None, model=None, options=None) -> AsyncIterator[dict]:
        body = {"model": model or self.cfg["model"], "messages": messages, "stream": True,
                "keep_alive": "30m", "options": self._options(options)}
        if tools:
            body["tools"] = tools
        first_t = float(self.cfg.get("first_token_timeout", 240))
        idle_t = float(self.cfg.get("idle_timeout", 60))
        try:
            async with self.http.stream("POST", f"{self.base}/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    if r.status_code == 404:
                        raise ProviderError(
                            f"Model '{body['model']}' isn't installed. Run: ollama pull {body['model']}")
                    raise ProviderError(f"Ollama error {r.status_code}: {detail}")
                lines = r.aiter_lines().__aiter__()
                first = True
                while True:
                    try:
                        line = await asyncio.wait_for(lines.__anext__(), first_t if first else idle_t)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise ProviderError(
                            f"The model stopped responding (no output for {int(first_t if first else idle_t)}s).")
                    first = False
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise ProviderError(f"Ollama: {data['error']}")
                    msg = data.get("message") or {}
                    chunk: dict = {}
                    if msg.get("content"):
                        chunk["content"] = msg["content"]
                    if msg.get("tool_calls"):
                        chunk["tool_calls"] = [
                            {"name": c["function"]["name"], "arguments": c["function"].get("arguments", {})}
                            for c in msg["tool_calls"]]
                    if data.get("done"):
                        chunk["usage"] = {"prompt": data.get("prompt_eval_count", 0),
                                          "completion": data.get("eval_count", 0)}
                        chunk["truncated"] = data.get("done_reason") == "length"
                    if chunk:
                        yield chunk
        except httpx.ConnectError:
            raise ProviderError(
                f"Can't reach Ollama at {self.base}. Start it with `ollama serve` (or open the Ollama app).")
        except httpx.ReadTimeout:
            raise ProviderError("Ollama took too long to respond.")

    async def health(self) -> dict:
        try:
            r = await self.http.get(f"{self.base}/api/tags", timeout=3)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            want = self.cfg["model"]
            present = want in names or f"{want}:latest" in names
            return {"ok": True, "model_present": present, "models": names,
                    "error": None if present else f"Model '{want}' not installed. Run: ollama pull {want}"}
        except Exception as e:
            return {"ok": False, "model_present": False, "models": [],
                    "error": f"Can't reach Ollama at {self.base} ({type(e).__name__})."}
