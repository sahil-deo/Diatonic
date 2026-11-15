"""Saved models you can switch between from the status bar: local (Ollama) or cloud (any OpenAI-compatible API).

The active model is copied into the `agent` settings when chosen, so everything that reads them (the agent loop,
spoken summaries, scheduled jobs, telemetry) uses it everywhere at once. API keys live in the keychain, one per
API host, never in the config file or a prompt.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import secrets

PRESETS = {
    "ollama": {"label": "Ollama (on this computer)", "provider": "ollama", "endpoint": "http://localhost:11434", "context": 8192},
    "gemini": {"label": "Google AI Studio (Gemini)", "provider": "openai", "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai", "context": 32768},
    "groq": {"label": "Groq", "provider": "openai", "endpoint": "https://api.groq.com/openai/v1", "context": 32768},
    "openai": {"label": "Other OpenAI-compatible API", "provider": "openai", "endpoint": "", "context": 16384},
}
PROVIDERS = ("ollama", "openai")
# What a saved model sets on the agent when it's chosen; the rest of the agent settings (timeouts, budgets) stay shared.
FIELDS = ("provider", "model", "endpoint")


def is_local(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def is_cloud(m: dict) -> bool:
    return not is_local(m.get("endpoint") or "")


def key_name(endpoint: str) -> str:
    return "model_key:" + (urlparse(endpoint).hostname or endpoint)


def has_key(endpoint: str) -> bool:
    return bool(secrets.get(key_name(endpoint)))


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "model"


def ensure(config) -> list[dict]:
    """First run with this feature: the model already configured becomes the first saved one."""
    d = config.data
    if not d.get("models"):
        a = d["agent"]
        m = {"id": slug(a["model"]), "label": a["model"], "provider": a["provider"], "model": a["model"],
             "endpoint": a["endpoint"], "context": int(a.get("num_ctx") or 8192), "max_tokens": int(a.get("max_tokens") or 1024)}
        d["models"] = [m]
        d["agent"]["active_model"] = m["id"]
        config.save()
    return d["models"]


def clean(m: dict, existing: list[dict]) -> dict:
    """Validate a saved-model definition from the Settings form."""
    preset = PRESETS.get(m.get("preset") or "")
    provider = m.get("provider") or (preset or {}).get("provider") or "openai"
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}.")
    endpoint = str(m.get("endpoint") or (preset or {}).get("endpoint") or "").strip().rstrip("/")
    u = urlparse(endpoint)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("The endpoint must be a full address, e.g. https://api.groq.com/openai/v1")
    if u.scheme == "http" and not is_local(endpoint):
        raise ValueError("Cloud endpoints must use https:// (the API key would otherwise travel unencrypted).")
    name = str(m.get("model") or "").strip()
    if not name or len(name) > 120 or any(c.isspace() for c in name):
        raise ValueError("Enter the model name exactly as the provider lists it, e.g. qwen3:8b or gemini-2.5-flash.")
    try:
        context = max(2048, min(2_000_000, int(m.get("context") or (preset or {}).get("context") or 8192)))
        max_tokens = max(256, min(32768, int(m.get("max_tokens") or 1024)))
    except (TypeError, ValueError):
        raise ValueError("Context and reply length must be numbers.")
    label = str(m.get("label") or name).strip()[:40]
    mid = m.get("id") or slug(f"{label}")
    if not m.get("id"):   # a new one: keep ids unique
        base, n = mid, 2
        while any(x["id"] == mid for x in existing):
            mid, n = f"{base}-{n}", n + 1
    return {"id": mid, "label": label, "provider": provider, "model": name, "endpoint": endpoint,
            "context": context, "max_tokens": max_tokens}


def activate(config, mid: str) -> dict:
    m = next((x for x in ensure(config) if x["id"] == mid), None)
    if m is None:
        raise ValueError("No saved model with that id.")
    config.update({"agent": {**{k: m[k] for k in FIELDS}, "num_ctx": m["context"], "max_tokens": m["max_tokens"], "active_model": m["id"]}})
    return m


def active(config) -> dict:
    a = config.data["agent"]
    return next((x for x in ensure(config) if x["id"] == a.get("active_model")), None) or {
        "id": "", "label": a["model"], "provider": a["provider"], "model": a["model"], "endpoint": a["endpoint"]}


def public(config) -> dict:
    """For the UI: saved models with cloud/key status, never the keys themselves."""
    saved = ensure(config)   # first: it may set the active model
    act = config.data["agent"].get("active_model")
    return {"active": act, "presets": PRESETS,
            "models": [{**m, "cloud": is_cloud(m), "key_set": has_key(m["endpoint"]) if is_cloud(m) or m["provider"] == "openai" else None,
                        "active": m["id"] == act} for m in saved]}
