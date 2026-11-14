"""Local neural text-to-speech with Piper."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import wave

from ..config import home

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_files(voice: str) -> tuple[str, list[str]]:
    """'en_US-lessac-medium' -> repo subpath and the two filenames."""
    lang_region, name, quality = voice.split("-", 2)
    sub = f"{lang_region.split('_')[0]}/{lang_region}/{name}/{quality}"
    return sub, [f"{voice}.onnx", f"{voice}.onnx.json"]


class TTS:
    def __init__(self):
        self._voices: dict = {}

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("piper") is not None

    @staticmethod
    def dir():
        d = home() / "models" / "piper"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ready(self, voice: str) -> bool:
        return self.available() and (self.dir() / f"{voice}.onnx").exists() \
            and (self.dir() / f"{voice}.onnx.json").exists()

    def _load(self, voice: str):
        if voice not in self._voices:
            from piper import PiperVoice

            self._voices[voice] = PiperVoice.load(str(self.dir() / f"{voice}.onnx"))
        return self._voices[voice]

    def _run(self, text: str, voice: str, speed: float) -> bytes:
        v = self._load(voice)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            if hasattr(v, "synthesize_wav"):  # piper >= 1.3
                from piper import SynthesisConfig

                v.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=1.0 / max(speed, 0.25)))
            else:  # older API
                v.synthesize(text, wf, length_scale=1.0 / max(speed, 0.25))
        return buf.getvalue()

    async def synth(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        return await asyncio.get_running_loop().run_in_executor(None, self._run, text, voice, speed)
