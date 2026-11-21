"""LLM providers. Add one by implementing `stream`/`complete`/`health` and registering it."""
from .antigravity import AntigravityProvider
from .base import Provider, ProviderError
from .ollama import OllamaProvider
from .openai import OpenAICompatProvider

PROVIDERS: dict[str, type[Provider]] = {"ollama": OllamaProvider, "openai": OpenAICompatProvider, "antigravity": AntigravityProvider}


def make_provider(ctx, model: dict | None = None) -> Provider:
    """The provider for the active model (or for `model`, a saved-model definition, e.g. to test it)."""
    from ... import secrets
    from ...models import key_name
    cfg = dict(ctx.config.data["agent"])
    if model:
        cfg.update({k: model[k] for k in ("provider", "model", "endpoint")}, num_ctx=model.get("context", cfg["num_ctx"]),
                   max_tokens=model.get("max_tokens", cfg["max_tokens"]))
    cls = PROVIDERS.get(cfg["provider"])
    if cls is None:
        raise ProviderError(f"Unknown provider '{cfg['provider']}'.")
    if cfg["provider"] == "antigravity":
        cfg["command"] = ctx.config.data.get("antigravity", {}).get("command") or "agy"
    elif cfg["provider"] != "ollama":
        cfg["api_key"] = secrets.get(key_name(cfg["endpoint"]))
    return cls(ctx.http, cfg)


__all__ = ["PROVIDERS", "Provider", "ProviderError", "make_provider"]
