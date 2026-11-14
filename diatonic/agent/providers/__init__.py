"""LLM providers. Add one by implementing `stream`/`complete`/`health` and registering it."""
from .base import Provider, ProviderError
from .ollama import OllamaProvider

PROVIDERS: dict[str, type[Provider]] = {"ollama": OllamaProvider}


def make_provider(ctx) -> Provider:
    cfg = ctx.config.data["agent"]
    cls = PROVIDERS.get(cfg["provider"])
    if cls is None:
        raise ProviderError(f"Unknown provider '{cfg['provider']}'.")
    return cls(ctx.http, cfg)


__all__ = ["PROVIDERS", "Provider", "ProviderError", "make_provider"]
