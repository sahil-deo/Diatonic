"""Panel and read-only tool over the telemetry the harness records about itself."""
from __future__ import annotations

from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..base import App, Tool


class TelemetryApp(App):
    id = "telemetry"
    name = "Telemetry"
    icon = "activity"
    description = "How fast and how reliable the agent is: latency, tokens, cache hits, tool errors, guard events."
    panel = True
    keywords = ["telemetry", "performance", "latency", "slow", "slowest", "speed", "how fast", "how long did", "cache"]

    def tools(self) -> list[Tool]:
        return [Tool("performance_summary", "Summarise recent agent speed and reliability.",
                     {"properties": {"days": {"type": "integer", "minimum": 1, "maximum": 30}}}, self.performance_summary)]

    async def performance_summary(self, a):
        s = self.ctx.telemetry.summary(a.get("days", 1))
        if not s["runs"]:
            return "No runs recorded in that period."
        sec = lambda ms: None if ms is None else round(ms / 1000, 1)
        return {"runs": s["runs"], "errors": s["errors"], "median_seconds": sec(s["p50_ms"]), "p90_seconds": sec(s["p90_ms"]),
                "first_output_seconds": sec(s["ttft_p50_ms"]), "generation_tokens_per_s": s["gen_tok_s"],
                "cache_hit_pct": s["cache_pct"], "tool_errors": s["tool_errors"],
                "slowest_tool": (max(s["tools"], key=lambda t: t["avg_ms"])["name"] if s["tools"] else None),
                "guard_events": {g["kind"]: g["count"] for g in s["guards"][:5]} or None}

    # ---- panel routes --------------------------------------------------------
    def _days(self, request: Request, default: float = 1) -> float:
        try:
            return max(0.04, min(30.0, float(request.query_params.get("days", default))))
        except ValueError:
            return default

    async def summary(self, request: Request):
        return JSONResponse({**self.ctx.telemetry.summary(self._days(request)), "cfg": self.ctx.config.data["telemetry"]})

    async def runs(self, request: Request):
        q = request.query_params
        return JSONResponse(self.ctx.telemetry.runs(self._days(request, 7), min(int(q.get("limit", 30)), 100),
                                                    q.get("sort", "recent"), q.get("status")))

    async def run(self, request: Request):
        r = self.ctx.telemetry.run(int(request.path_params["rid"]))
        if not r:
            return JSONResponse({"error": "No such run"}, 404)
        return JSONResponse(r)

    async def clear(self, request: Request):
        self.ctx.telemetry.clear()
        return JSONResponse({"ok": True})

    def routes(self):
        return [Route("/summary", self.summary), Route("/runs", self.runs), Route("/run/{rid}", self.run),
                Route("/clear", self.clear, methods=["POST"])]
