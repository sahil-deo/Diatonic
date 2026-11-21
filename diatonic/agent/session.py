"""Which browser window started the current run, for tools that act on the screen."""
from __future__ import annotations

from contextvars import ContextVar

# Set by RunManager for the lifetime of a run. None for scheduled and other background runs, which have no screen.
RUN_CLIENT: ContextVar[str | None] = ContextVar("run_client", default=None)

# A scheduled job's run: the exact shell commands the user pre-approved for it (set by the scheduler).
JOB: ContextVar[dict | None] = ContextVar("run_job", default=None)
# The tool call being executed, so a long-running tool (the shell) can stream output into its card.
CALL: ContextVar[tuple | None] = ContextVar("run_call", default=None)

# What the user asked in this run, for tools that check they're the right tool for it (an app for meetings isn't a local list).
REQUEST: ContextVar[str] = ContextVar("run_request", default="")

# Set when a run has read content written by someone else (an email). Such a run may not change the interface.
UNTRUSTED: ContextVar[dict | None] = ContextVar("run_untrusted", default=None)


UNTRUSTED_NOTE = "This run has read email, whose text anyone can write, so it can't change the interface. Ask again in a new chat."


def mark_untrusted(source: str) -> None:
    box = UNTRUSTED.get()
    if box is not None:
        box[source] = True


def untrusted_sources() -> list[str]:
    return sorted(UNTRUSTED.get() or {})
