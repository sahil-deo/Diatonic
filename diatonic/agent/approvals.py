"""Pending approvals: the agent loop awaits a future the UI resolves."""
from __future__ import annotations

import asyncio

_pending: dict[str, asyncio.Future] = {}


def request(approval_id: str) -> asyncio.Future:
    fut = asyncio.get_running_loop().create_future()
    _pending[approval_id] = fut
    return fut


def resolve(approval_id: str, approved: bool | str) -> bool:
    """approved: True / False, or "chat" = yes, and don't ask again in this chat (cloud sharing)."""
    fut = _pending.get(approval_id)
    if fut is None or fut.done():
        return False
    fut.set_result(approved if approved == "chat" else bool(approved))
    return True


def discard(approval_id: str) -> None:
    fut = _pending.pop(approval_id, None)
    if fut and not fut.done():
        fut.cancel()
