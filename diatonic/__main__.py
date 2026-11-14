from __future__ import annotations

import argparse
import asyncio
import webbrowser

from .config import Config


def main() -> None:
    ap = argparse.ArgumentParser(prog="diatonic", description="Local agentic harness")
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="start the server (default)")
    run.add_argument("--port", type=int)
    run.add_argument("--open", action="store_true", help="open the UI in your browser")
    sub.add_parser("setup-voice", help="download the Whisper and Piper models")
    ap.add_argument("--port", type=int)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    cfg = Config()
    if args.cmd == "setup-voice":
        from .voice.models import VoiceSetup
        from .voice.stt import STT
        from .voice.tts import TTS

        s = VoiceSetup(STT(), TTS())
        v = cfg.data["voice"]
        asyncio.run(s.run(v["stt_model"], v["tts_voice"]))
        print(s.state["message"])
        raise SystemExit(0 if s.state["state"] == "done" else 1)

    import uvicorn

    from .server import create_app

    port = args.port or cfg.data["server"]["port"]
    cfg.data["server"]["port"] = port
    host = "127.0.0.1"
    url = f"http://localhost:{port}"
    print(f"\n  Diatonic  →  {url}\n")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning", timeout_graceful_shutdown=2)


if __name__ == "__main__":
    main()
