"""Secret storage: macOS Keychain (via keyring) with a 0600 file fallback."""
from __future__ import annotations

import json
import os

from .config import home

SERVICE = "diatonic"


def _file_backend() -> bool:
    return os.environ.get("DIATONIC_SECRETS_BACKEND") == "file"


def _file():
    return home() / "secrets.json"


def _read_file() -> dict:
    try:
        return json.loads(_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(d: dict) -> None:
    p = _file()
    p.write_text(json.dumps(d))
    os.chmod(p, 0o600)


def get(name: str) -> str | None:
    if not _file_backend():
        try:
            import keyring

            v = keyring.get_password(SERVICE, name)
            if v is not None:
                return v
        except Exception:
            pass
    return _read_file().get(name)


def set(name: str, value: str) -> None:  # noqa: A001
    if not value:
        return delete(name)
    if not _file_backend():
        try:
            import keyring

            keyring.set_password(SERVICE, name, value)
            return
        except Exception:
            pass
    d = _read_file()
    d[name] = value
    _write_file(d)


def delete(name: str) -> None:
    if not _file_backend():
        try:
            import keyring

            keyring.delete_password(SERVICE, name)
        except Exception:
            pass
    d = _read_file()
    if name in d:
        del d[name]
        _write_file(d)
