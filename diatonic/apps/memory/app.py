"""Lets the user tell the agent to remember or forget a fact ("remember that my default repo is …")."""
from __future__ import annotations

from ...memory import valid_scope
from ..base import WRITE, App, Tool, ToolError


class MemoryApp(App):
    id = "memory"
    name = "Memory"
    icon = "brain"
    description = "Short facts the agent keeps across chats. Manage them in Settings → Memory."
    keywords = ["remember", "forget", "memory", "memories", "memorize", "from now on", "keep in mind"]
    default_allow: list[str] = []   # persisting something across chats should be a conscious yes

    def tools(self) -> list[Tool]:
        return [
            Tool("remember", "Save one short fact about the user or their setup for future chats. Only when the user asks "
                 "you to remember something, never on your own.",
                 {"properties": {"text": {"type": "string", "description": "One short sentence, under 200 characters"},
                                 "scope": {"type": "string", "description": "'global', or an app id like 'github' when it only matters there"}},
                  "required": ["text"]}, self.remember, WRITE,
                 describe=lambda a: f"Remember{' (' + a['scope'] + ')' if a.get('scope') not in (None, '', 'global') else ''}: “{a.get('text')}”"),
            Tool("forget", "Delete a remembered fact. Pass words from it.",
                 {"properties": {"text": {"type": "string"}}, "required": ["text"]}, self.forget, WRITE,
                 describe=lambda a: f"Forget: “{a.get('text')}”"),
            Tool("list_memories", "Show what is remembered.", {}, self.list_memories),
        ]

    async def remember(self, a):
        scope = (a.get("scope") or "global").strip().lower()
        if scope != "global" and not scope.startswith(("app:", "tool:")):
            scope = f"app:{scope}"
        known = {x.id for x in self.ctx.scheduler.registry.apps.values()} if self.ctx.scheduler else None
        if not valid_scope(scope, known):
            raise ToolError(f"There's no app called '{scope.split(':', 1)[-1]}'. Use global or an app id.")
        try:
            row, status = self.ctx.memory.add(a["text"], scope=scope, source="agent")
        except ValueError as e:
            raise ToolError(str(e))
        self.ctx.broadcast({"type": "app_changed", "app": "memory"})
        return {"remembered": row["text"], "scope": row["scope"]} if status == "created" else {"already_known": row["text"]}

    async def forget(self, a):
        row = self.ctx.memory.forget_matching(a["text"])
        if not row:
            raise ToolError("Nothing remembered matches that. Use list_memories to see what's saved.")
        self.ctx.broadcast({"type": "app_changed", "app": "memory"})
        return {"forgot": row["text"]}

    async def list_memories(self, a):
        rows = [r for r in self.ctx.memory.all() if r["kind"] == "fact"]
        return [{"text": r["text"], "scope": r["scope"]} for r in rows] or "Nothing is remembered yet."
