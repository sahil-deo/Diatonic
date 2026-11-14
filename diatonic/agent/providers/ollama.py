from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import Provider, ProviderError


class OllamaProvider(Provider):
    @property
    def base(self) -> str:
        return self.cfg["endpoint"].rstrip("/")

    async def stream(self, messages, tools=None, model=None) -> AsyncIterator[dict]:
        body = {
            "model": model or self.cfg["model"],
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {"temperature": self.cfg["temperature"], "num_ctx": self.cfg["num_ctx"]},
        }
        if tools:
            body["tools"] = tools
        try:
            async with self.http.stream("POST", f"{self.base}/api/chat", json=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    if r.status_code == 404:
                        raise ProviderError(
                            f"Model '{body['model']}' isn't installed. Run: ollama pull {body['model']}"
                        )
                    raise ProviderError(f"Ollama error {r.status_code}: {detail}")
                async for line in r.aiter_lines():
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
                            for c in msg["tool_calls"]
                        ]
                    if chunk:
                        yield chunk
        except httpx.ConnectError:
            raise ProviderError(
                f"Can't reach Ollama at {self.base}. Start it with `ollama serve` (or open the Ollama app)."
            )
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
