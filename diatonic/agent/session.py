"""Which browser window started the current run, for tools that act on the screen."""
from __future__ import annotations

from contextvars import ContextVar

# Set by RunManager for the lifetime of a run. None for scheduled and other background runs, which have no screen.
RUN_CLIENT: ContextVar[str | None] = ContextVar("run_client", default=None)

# Set when a run has read content written by someone else (an email). Such a run may not change the interface.
UNTRUSTED: ContextVar[dict | None] = ContextVar("run_untrusted", default=None)


def mark_untrusted(source: str) -> None:
    box = UNTRUSTED.get()
    if box is not None:
        box[source] = True


def untrusted_sources() -> list[str]:
    return sorted(UNTRUSTED.get() or {})
