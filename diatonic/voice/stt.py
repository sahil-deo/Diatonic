"""Local speech-to-text with faster-whisper (English-only models)."""
from __future__ import annotations

import asyncio
import importlib.util

from ..config import home


class STT:
    def __init__(self):
        self._model = None
        self._name = None

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec("numpy") is not None

    @staticmethod
    def root() -> str:
        return str(home() / "models" / "whisper")

    def ready(self, name: str) -> bool:
        if not self.available():
            return False
        try:
            from faster_whisper.utils import download_model

            download_model(name, local_files_only=True, cache_dir=self.root())
            return True
        except Exception:
            return False

    def _load(self, name: str):
        if self._model is None or self._name != name:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(name, device="cpu", compute_type="int8", download_root=self.root())
            self._name = name
        return self._model

    def _run(self, pcm: bytes, name: str) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._load(name).transcribe(
            audio, language="en", beam_size=1, vad_filter=True, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, pcm: bytes, name: str) -> str:
        """pcm: 16 kHz mono signed 16-bit little-endian."""
        return await asyncio.get_running_loop().run_in_executor(None, self._run, pcm, name)
