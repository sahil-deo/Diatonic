"""Tests for rule-based model routing: classification, model resolution, custom rules, and API endpoints."""
import asyncio
import json
import pytest
from starlette.testclient import TestClient

from conftest import ScriptedProvider
from diatonic.agent import loop, router
from diatonic.config import Config
from diatonic.context import Context
from diatonic.db import Database
from diatonic import models as M
from diatonic.server import create_app


@pytest.fixture
def test_ctx(tmp_path):
    c = Config(tmp_path / "config.json")
    db = Database(tmp_path / "test.db")
    ctx = Context(c, db)
    return ctx


# ---------- Classification tests ----------
def test_classification_ui(test_ctx):
    prompts = [
        "open mail panel in split",
        "show calendar on the right",
        "zoom the mail app",
        "unzoom layout",
        "hide sidebar",
        "switch to todos",
        "make the notes tab full screen",
    ]
    for p in prompts:
        cat, name = router.classify_task(test_ctx, p)
        assert cat == "ui", f"Prompt '{p}' should be classified as 'ui', got '{cat}'"


def test_classification_builder(test_ctx):
    prompts = [
        "create a calculator app",
        "make a stopwatch app with lap times",
        "build an app for tracking daily water intake",
        "/app-creator build a pomodoro timer",
        "an app that generates random passwords",
    ]
    for p in prompts:
        cat, name = router.classify_task(test_ctx, p)
        assert cat == "builder", f"Prompt '{p}' should be classified as 'builder', got '{cat}'"


def test_classification_complex_research_and_code(test_ctx):
    prompts = [
        "do an in-depth research report on quantum computing algorithms",
        "deep dive into the current memory leak in the worker pool",
        "audit the repo for potential security vulnerabilities",
        "analyze all git commits this week and explain the diff",
        "code analysis of the authentication pipeline",
        "plan a migration from SQLite to Postgres",
    ]
    for p in prompts:
        cat, name = router.classify_task(test_ctx, p)
        assert cat == "complex", f"Prompt '{p}' should be classified as 'complex', got '{cat}'"


def test_classification_multi_app(test_ctx):
    prompts = [
        "check my calendar for conflicts and draft email responses to Alex",
        "read my meeting notes and add todos for each action item",
        "find unread emails about invoices and save attachments to files",
        "look up customer notes then schedule a follow-up meeting on calendar",
    ]
    for p in prompts:
        cat, name = router.classify_task(test_ctx, p)
        assert cat == "multi_app", f"Prompt '{p}' should be classified as 'multi_app', got '{cat}'"


def test_classification_trivial(test_ctx):
    prompts = [
        "proofread this email draft: Dear Sir, hope you are fine",
        "translate this paragraph to Spanish",
        "rephrase this sentence to sound more formal",
        "correct the grammar in this message",
        "hello there",
        "what is 24 * 15?",
        "define ephemeral",
    ]
    for p in prompts:
        cat, name = router.classify_task(test_ctx, p)
        assert cat == "trivial", f"Prompt '{p}' should be classified as 'trivial', got '{cat}'"


def test_email_summarization_not_trivial(test_ctx):
    screen = {"app": "gmail", "kind": "email", "ref": "work@acme.test|18cb554", "title": "Quarterly Plan"}
    for p in [
        'Summarize the email "Quarterly Plan" from Alice <alice@acme.test> (ref: work@acme.test|18cb554)',
        "summarize this email",
        "summarize this mail",
        "summarize this",
    ]:
        cat, name = router.classify_task(test_ctx, p, screen=screen)
        assert cat != "trivial", f"Prompt '{p}' with open email on screen should not be 'trivial', got '{cat}'"

    for p in [
        'Summarize the email "Quarterly Plan" (ref: work@acme.test|18cb554)',
        "summarize this email",
        "summarize this mail",
    ]:
        cat, name = router.classify_task(test_ctx, p)
        assert cat != "trivial", f"Prompt '{p}' naming email should not be 'trivial', got '{cat}'"


def test_custom_rule_takes_precedence(test_ctx):
    test_ctx.config.data["model_routing"] = {
        "enabled": True,
        "rules": router.DEFAULT_RULES,
        "custom_rules": [
            {"id": "github-rule", "name": "GitHub Special", "pattern": r"\bgithub\b", "model": "gemini-model"}
        ],
    }
    cat, name = router.classify_task(test_ctx, "check my github issues and summarize")
    assert cat == "github-rule"
    assert name == "GitHub Special"


# ---------- Model resolution tests ----------
def test_resolution_when_routing_disabled(test_ctx):
    M.ensure(test_ctx.config)
    test_ctx.config.data["model_routing"]["enabled"] = False
    model_m, route_info = router.resolve_model(test_ctx, "open mail panel in split")
    assert route_info["enabled"] is False
    assert model_m["id"] == test_ctx.config.data["agent"]["active_model"]


def test_resolution_routes_to_target_model(test_ctx):
    # Setup saved models
    test_ctx.config.data["models"] = [
        {"id": "fast-ollama", "label": "Fast Ollama", "provider": "ollama", "model": "qwen3:4b", "endpoint": "http://localhost:11434", "context": 8192, "max_tokens": 1024},
        {"id": "potent-agy", "label": "Antigravity", "provider": "antigravity", "model": "default", "endpoint": "https://antigravity.google", "context": 1048576, "max_tokens": 4096},
        {"id": "mid-gemini", "label": "Gemini Flash", "provider": "openai", "model": "gemini-2.5-flash", "endpoint": "https://api.openai.com/v1", "context": 32768, "max_tokens": 2048},
    ]
    test_ctx.config.data["agent"]["active_model"] = "fast-ollama"
    test_ctx.config.data["model_routing"] = {
        "enabled": True,
        "rules": [
            {"id": "ui", "name": "UI & Navigation", "description": "", "model": "fast-ollama"},
            {"id": "trivial", "name": "Trivial & Text Tasks", "description": "", "model": "fast-ollama"},
            {"id": "multi_app", "name": "Multi-App Workflows", "description": "", "model": "mid-gemini"},
            {"id": "builder", "name": "App Creation", "description": "", "model": "potent-agy"},
            {"id": "complex", "name": "Deep Research & Code Analysis", "description": "", "model": "potent-agy"},
        ],
        "custom_rules": [],
    }

    # UI request -> fast-ollama
    m, info = router.resolve_model(test_ctx, "open mail panel in split")
    assert m["id"] == "fast-ollama"
    assert info["category"] == "ui"
    assert info["routed"] is True

    # Builder request -> potent-agy
    m, info = router.resolve_model(test_ctx, "create a timer app")
    assert m["id"] == "potent-agy"
    assert info["category"] == "builder"
    assert info["routed"] is True

    # Multi-app request -> mid-gemini
    m, info = router.resolve_model(test_ctx, "check calendar for tomorrow and draft email to Bob")
    assert m["id"] == "mid-gemini"
    assert info["category"] == "multi_app"
    assert info["routed"] is True

    # Fallback to active model when rule model is empty
    test_ctx.config.data["model_routing"]["rules"][0]["model"] = ""
    m, info = router.resolve_model(test_ctx, "open mail panel in split")
    assert m["id"] == "fast-ollama"
    assert info["fallback"] is True


def test_clean_routing_config():
    valid = router.clean_routing_config({
        "enabled": True,
        "rules": [
            {"id": "ui", "model": "m1"},
            {"id": "complex", "model": "m2"},
        ],
        "custom_rules": [
            {"id": "c1", "name": "Rule 1", "pattern": "code", "model": "m3"}
        ],
    })
    assert valid["enabled"] is True
    assert len(valid["rules"]) == 5  # all 5 default tiers included
    ui_rule = next(r for r in valid["rules"] if r["id"] == "ui")
    assert ui_rule["model"] == "m1"
    assert len(valid["custom_rules"]) == 1
    assert valid["custom_rules"][0]["pattern"] == "code"


# ---------- API endpoint tests ----------
def test_routing_api_endpoints(test_ctx):
    app = create_app(test_ctx.config, test_ctx.db, allowed_hosts={"testserver"})
    with TestClient(app) as client:
        # GET
        res = client.get("/api/models/routing")
        assert res.status_code == 200
        data = res.json()
        assert "enabled" in data
        assert len(data["rules"]) == 5

        # PUT
        put_data = {
            "enabled": True,
            "rules": [
                {"id": "ui", "model": "model-1"},
                {"id": "trivial", "model": ""},
                {"id": "multi_app", "model": "model-2"},
                {"id": "builder", "model": "model-3"},
                {"id": "complex", "model": "model-4"},
            ],
            "custom_rules": [{"id": "c1", "name": "Git", "pattern": "git", "model": "model-5"}],
        }
        res = client.put("/api/models/routing", json=put_data)
        assert res.status_code == 200
        saved = res.json()
        assert saved["enabled"] is True
        assert next(r for r in saved["rules"] if r["id"] == "ui")["model"] == "model-1"

        # TEST
        test_req = {"text": "open mail panel in split"}
        res = client.post("/api/models/routing/test", json=test_req)
        assert res.status_code == 200
        t_res = res.json()
        assert t_res["category"] == "ui"
        assert t_res["rule_name"] == "UI & Navigation"
