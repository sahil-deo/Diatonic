"""Rule-based model routing: classify user requests into task tiers and route to the best model.

Tiers:
- UI & Navigation (ui): Screen controls, layout adjustments, zoom, panels. (Fast / Local)
- Trivial & Text Tasks (trivial): Quick text edits, translations, proofreading, simple Q&A. (Fast / Lightweight)
- Multi-App Workflows (multi_app): Productivity tasks crossing multiple apps (mail, calendar, notes, todos). (Mid-tier)
- App Creation (builder): Mini-apps, /app-creator, fileapps, fixing app errors. (Potent Code Model)
- Deep Research & Code Analysis (complex): In-depth research, repo audits, diff analysis, deep reasoning. (Heavy Model)
- Custom Rules: User-defined keyword or regex rules evaluated first.
"""
from __future__ import annotations

import re
from typing import Any

from .. import models
from .loop import is_navigation, is_text_task

DEFAULT_RULES = [
    {
        "id": "ui",
        "name": "UI & Navigation",
        "description": "Opening panels, changing layouts, zoom, switching tabs",
        "model": "",
    },
    {
        "id": "trivial",
        "name": "Trivial & Text Tasks",
        "description": "Quick text tasks, translations, summaries, proofreading, simple Q&A",
        "model": "",
    },
    {
        "id": "multi_app",
        "name": "Multi-App Workflows",
        "description": "Everyday productivity tasks coordinating mail, calendar, notes, todos, etc.",
        "model": "",
    },
    {
        "id": "builder",
        "name": "App Creation",
        "description": "Creating and editing custom mini-apps, /app-creator",
        "model": "",
    },
    {
        "id": "complex",
        "name": "Deep Research & Code Analysis",
        "description": "In-depth research, repository code analysis, deep reasoning",
        "model": "",
    },
]

# Detection patterns for Deep Research and Complex Code Analysis
RE_COMPLEX = re.compile(
    r"\b(?:deep\s*dive|in[- ]depth\s+research|research\s+(?:on|about|into|the)|investigate\b|"
    r"audit\s+(?:the\s+)?(?:repo|repository|codebase|security)|analyze\s+(?:all\s+)?(?:git\s+)?(?:commits?|diff|security|architecture|codebase)|"
    r"code\s+analysis|security\s+(?:audit|review|regressions?)|architectural\s+review|"
    r"plan\s+(?:a\s+)?migration|critically\s+review)\b",
    re.I,
)

# App names or domains for Multi-App coordination detection
APP_KEYWORDS = {
    "mail": ("mail", "email", "emails", "inbox", "gmail"),
    "calendar": ("calendar", "events", "meeting", "meetings", "gcal", "schedule"),
    "notes": ("note", "notes"),
    "todos": ("todo", "todos", "task", "tasks", "checklist"),
    "files": ("file", "files", "folder", "folders", "document", "documents"),
    "github": ("github", "repo", "pr", "pull request", "issue", "issues"),
    "web": ("web", "browse", "website", "search online"),
    "terminal": ("terminal", "shell", "command", "bash"),
}

RE_COORDINATED_ACTION = re.compile(
    r"\b(?:check|read|find|search|look\s*up|get|fetch)\b.+\b(?:and|then|after\s+that)\b.+\b(?:draft|send|create|add|schedule|write|update|put)\b",
    re.I,
)

# Layout navigation keywords
RE_LAYOUT = re.compile(r"\b(?:split|zoom|unzoom|layout|fullscreen|full screen|sidebar)\b", re.I)

# App creation indicators (from builder.py)
RE_BUILD_SLASH = re.compile(r"^\W*/app-creator\b", re.I)
RE_BUILD_VERBS = re.compile(
    r"\b(?:create|make|build|implement|add)\b(?:\s+\w+){0,5}?\s+"
    r"(?:app|apps|panel|tab|calculator|converter|timer|stopwatch|clock|counter|game|generator|quiz|checklist|planner|tracker|viewer|editor)\b",
    re.I,
)
RE_NEW_APP = re.compile(r"^\W*an? (?:\w+ ){0,2}(?:app|panel)\b|\bapp (?:for|to|that)\b|\b(?:new|own|custom|my own) (?:app|panel|tab)\b", re.I)
RE_ERROR_PASTE = re.compile(
    r"\b(?:Uncaught|TypeError|ReferenceError|SyntaxError|RangeError|is not defined|is not a function|Cannot read propert\w+|Failed to fetch)\b"
    r"|\bat \S+ \(.+:\d+:\d+\)"
)


RE_UI_VERBS = re.compile(r"\b(?:zoom|unzoom|split|hide\s+sidebar|show\s+sidebar|toggle\s+sidebar|full\s*screen)\b", re.I)


def _matches_custom_rule(rule: dict, text: str) -> bool:
    pat = (rule.get("pattern") or "").strip()
    if not pat:
        return False
    try:
        return bool(re.search(pat, text, re.I))
    except re.error:
        return pat.lower() in text.lower()


def _is_builder(ctx, text: str, screen: dict | None, conv_id: str | None) -> bool:
    if RE_BUILD_SLASH.search(text):
        return True
    if RE_BUILD_VERBS.search(text) or RE_NEW_APP.search(text):
        return True
    fileapps = getattr(ctx, "fileapps", None)
    apps = fileapps.list_apps() if fileapps else []
    if apps and RE_ERROR_PASTE.search(text):
        return True
    if apps and any(re.search(rf"\b{re.escape(a['id'])}\b", text, re.I) for a in apps):
        if re.search(r"\b(?:update|edit|change|modify|fix|tweak|adjust|broken|bug|not working)\b", text, re.I):
            return True
    # Check if conversation is in active builder session
    if conv_id and hasattr(ctx, "db"):
        prior = ctx.db.tools_used(conv_id)
        if any(t in prior for t in ("ui_create_file_app", "ui_write_app_file", "ui_patch_app_file")):
            if not re.match(r"^\W*(?:thanks?|thank you|done|that'?s (?:all|it)|stop|exit)\b", text, re.I):
                return True
    return False


def _is_multi_app(text: str) -> bool:
    low = text.lower()
    matched_domains = set()
    for domain, kws in APP_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", low) for kw in kws):
            matched_domains.add(domain)
    if len(matched_domains) >= 2:
        return True
    return bool(matched_domains and RE_COORDINATED_ACTION.search(text))


def _is_trivial(text: str) -> bool:
    if is_text_task(text):
        return True
    stripped = text.strip()
    if len(stripped) < 60:
        if re.match(r"^\W*(?:hi|hello|hey|greetings|good\s+(?:morning|afternoon|evening|day)|thanks|thank you|how are you|who are you)\b", stripped, re.I):
            return True
        if re.match(r"^\W*(?:what\s+is\s+)?\d+\s*[\+\-\*\/]\s*\d+\s*\??$", stripped, re.I):
            return True
        if re.match(r"^\W*(?:define|meaning of|what does\s+\w+\s+mean)\b", stripped, re.I):
            return True
    return False


def _is_ui(text: str, screen: dict | None) -> bool:
    if re.search(r"\b(?:create|make|build)\s+(?:an?\s+)?(?:\w+\s+){0,3}apps?\b", text, re.I):
        return False
    if is_navigation(text):
        return True
    if RE_UI_VERBS.search(text):
        return True
    if RE_LAYOUT.search(text) and re.search(r"\b(?:open|put|place|make|set|show|switch|move|tab|window|panel|screen|app)\b", text, re.I):
        return True
    return False


def classify_task(ctx, text: str, screen: dict | None = None, conv_id: str | None = None) -> tuple[str, str]:
    """Determine the task category and rule name for a given request.

    Returns (category_id, rule_name).
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return "default", "Default active model"

    # 1. Custom rules configured by the user
    routing_cfg = ctx.config.data.get("model_routing", {}) if hasattr(ctx, "config") else {}
    for rule in routing_cfg.get("custom_rules", []):
        if _matches_custom_rule(rule, clean_text):
            return rule.get("id", "custom"), rule.get("name") or "Custom rule"

    # 2. UI Navigation & Layout
    if _is_ui(clean_text, screen):
        return "ui", "UI & Navigation"

    # 3. App Creation & Builder
    if _is_builder(ctx, clean_text, screen, conv_id):
        return "builder", "App Creation"

    # 4. Deep Research & Code Analysis
    if RE_COMPLEX.search(clean_text):
        return "complex", "Deep Research & Code Analysis"

    # 5. Multi-App Workflows
    if _is_multi_app(clean_text):
        return "multi_app", "Multi-App Workflows"

    # 6. Trivial & Quick Text Tasks
    if _is_trivial(clean_text):
        return "trivial", "Trivial & Text Tasks"

    # 7. Fallback to default
    return "default", "Default active model"


def resolve_model(ctx, text: str, screen: dict | None = None, conv_id: str | None = None) -> tuple[dict, dict]:
    """Resolve which model to use for a request based on routing rules.

    Returns (model_dict, route_info).
    """
    cfg = ctx.config
    routing_cfg = cfg.data.get("model_routing", {})
    active_m = models.active(cfg)

    if not routing_cfg.get("enabled", False):
        return active_m, {
            "category": "default",
            "rule_name": "Default active model",
            "model_id": active_m.get("id", ""),
            "model_label": active_m.get("label", active_m.get("model", "")),
            "enabled": False,
        }

    cat_id, rule_name = classify_task(ctx, text, screen=screen, conv_id=conv_id)
    target_id = ""

    # Look up target model ID
    if cat_id.startswith("custom-") or any(r.get("id") == cat_id for r in routing_cfg.get("custom_rules", [])):
        custom_rule = next((r for r in routing_cfg.get("custom_rules", []) if r.get("id") == cat_id), None)
        if custom_rule:
            target_id = custom_rule.get("model", "")
    else:
        rule = next((r for r in routing_cfg.get("rules", []) if r.get("id") == cat_id), None)
        if rule:
            target_id = rule.get("model", "")

    saved_models = models.ensure(cfg)
    target_m = next((m for m in saved_models if m["id"] == target_id), None) if target_id else None

    if target_m:
        return target_m, {
            "category": cat_id,
            "rule_name": rule_name,
            "model_id": target_m["id"],
            "model_label": target_m.get("label", target_m.get("model", "")),
            "enabled": True,
            "routed": True,
        }

    return active_m, {
        "category": cat_id,
        "rule_name": rule_name,
        "model_id": active_m.get("id", ""),
        "model_label": active_m.get("label", active_m.get("model", "")),
        "enabled": True,
        "fallback": True,
    }


def clean_routing_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize a routing configuration update."""
    if not isinstance(data, dict):
        raise ValueError("Routing configuration must be an object.")

    enabled = bool(data.get("enabled", False))
    raw_rules = data.get("rules")
    if raw_rules is not None and not isinstance(raw_rules, list):
        raise ValueError("rules must be an array.")

    valid_ids = {"ui", "trivial", "multi_app", "builder", "complex"}
    rules_out = []
    defaults_by_id = {r["id"]: r for r in DEFAULT_RULES}

    for r in (raw_rules or DEFAULT_RULES):
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").strip()
        if rid in valid_ids:
            base = defaults_by_id.get(rid, {})
            rules_out.append({
                "id": rid,
                "name": str(r.get("name") or base.get("name", rid))[:60],
                "description": str(r.get("description") or base.get("description", ""))[:200],
                "model": str(r.get("model") or "").strip()[:60],
            })

    # Ensure all default categories are represented
    existing_ids = {r["id"] for r in rules_out}
    for def_r in DEFAULT_RULES:
        if def_r["id"] not in existing_ids:
            rules_out.append(dict(def_r))

    # Custom rules
    raw_custom = data.get("custom_rules")
    if raw_custom is not None and not isinstance(raw_custom, list):
        raise ValueError("custom_rules must be an array.")

    custom_out = []
    for i, cr in enumerate(raw_custom or []):
        if not isinstance(cr, dict):
            continue
        pat = str(cr.get("pattern") or "").strip()[:200]
        if not pat:
            continue
        cid = str(cr.get("id") or f"custom-{i+1}").strip()[:40]
        cname = str(cr.get("name") or pat)[:60]
        cmodel = str(cr.get("model") or "").strip()[:60]
        custom_out.append({"id": cid, "name": cname, "pattern": pat, "model": cmodel})

    return {"enabled": enabled, "rules": rules_out, "custom_rules": custom_out}
