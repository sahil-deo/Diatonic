"""One-shot downloader for the voice models (Whisper small.en + a Piper voice)."""
from __future__ import annotations

import asyncio

import httpx

from .stt import STT
from .tts import HF, TTS, voice_files


class VoiceSetup:
    def __init__(self, stt: STT, tts: TTS):
        self.stt, self.tts = stt, tts
        self.state = {"state": "idle", "message": ""}  # idle | running | done | error

    async def run(self, stt_model: str, voice: str) -> None:
        if self.state["state"] == "running":
            return
        self.state = {"state": "running", "message": "Starting…"}
        try:
            if not self.tts.available() or not self.stt.available():
                raise RuntimeError("Voice packages not installed. Run: pip install 'diatonic[voice]'")
            sub, files = voice_files(voice)
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as http:
                for f in files:
                    dest = self.tts.dir() / f
                    if dest.exists():
                        continue
                    self.state["message"] = f"Downloading Piper voice ({f})…"
                    r = await http.get(f"{HF}/{sub}/{f}")
                    r.raise_for_status()
                    dest.write_bytes(r.content)
            if not self.stt.ready(stt_model):
                self.state["message"] = f"Downloading Whisper {stt_model} (this can take a minute)…"
                from faster_whisper.utils import download_model

                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: download_model(stt_model, cache_dir=self.stt.root()))
            self.state = {"state": "done", "message": "Voice models ready."}
        except Exception as e:
            self.state = {"state": "error", "message": f"{type(e).__name__}: {e}"}
