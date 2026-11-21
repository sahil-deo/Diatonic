"""Tests for the GitHub client app and panel routes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app
from diatonic.apps.github import create as make_github


def test_github_app_has_expanded_tools():
    ctx = type("C", (), {})()
    app = make_github(ctx)
    tool_names = {t.name for t in app.tools()}
    assert {
        "list_repos", "get_repo", "update_repo",
        "list_issues", "search_issues", "get_issue", "create_issue", "comment_issue", "close_issue",
        "list_pulls", "get_pull", "get_pull_diff", "merge_pull",
        "create_pull", "close_pull", "list_branches", "create_branch", "delete_branch"
    } <= tool_names


def test_github_app_has_panel_routes():
    ctx = type("C", (), {})()
    app = make_github(ctx)
    paths = {r.path for r in app.routes()}
    assert {
        "/panel", "/repos",
        "/repo/{owner}/{name}",
        "/repo/{owner}/{name}/readme",
        "/repo/{owner}/{name}/branches",
        "/repo/{owner}/{name}/branches/{branch:path}",
        "/repo/{owner}/{name}/pulls",
        "/repo/{owner}/{name}/pulls/{number:int}",
        "/repo/{owner}/{name}/pulls/{number:int}/diff",
        "/repo/{owner}/{name}/pulls/{number:int}/merge",
        "/repo/{owner}/{name}/pulls/{number:int}/close",
        "/repo/{owner}/{name}/pulls/{number:int}/comment",
        "/repo/{owner}/{name}/issues",
        "/repo/{owner}/{name}/issues/{number:int}",
        "/repo/{owner}/{name}/issues/{number:int}/close",
        "/repo/{owner}/{name}/issues/{number:int}/comment",
    } <= paths


def test_github_tools_execution():
    ctx = type("C", (), {})()
    app = make_github(ctx)
    app._repo = AsyncMock(side_effect=lambda r: r)
    app._req = AsyncMock()

    # list_branches
    app._req.return_value = [{"name": "main", "protected": True, "commit": {"sha": "abc1234"}}]
    branches = asyncio.run(app.list_branches({"repo": "octocat/Hello-World", "limit": 10}))
    assert len(branches) == 1 and branches[0]["name"] == "main"
    app._req.assert_called_with("GET", "/repos/octocat/Hello-World/branches", params={"per_page": 10})

    # create_branch
    app._req.side_effect = [
        {"object": {"sha": "base123"}}, # get ref
        {"ref": "refs/heads/feature", "object": {"sha": "base123"}} # create ref
    ]
    cb = asyncio.run(app.create_branch({"repo": "octocat/Hello-World", "branch": "feature", "from_branch": "main"}))
    assert cb["created"] == "feature" and cb["sha"] == "base123"

    # delete_branch
    app._req.side_effect = None
    app._req.return_value = {}
    db = asyncio.run(app.delete_branch({"repo": "octocat/Hello-World", "branch": "feature"}))
    assert db["deleted"] == "feature"
    app._req.assert_called_with("DELETE", "/repos/octocat/Hello-World/git/refs/heads/feature")

    # create_pull
    app._req.return_value = {"number": 42, "html_url": "https://github.com/octocat/Hello-World/pull/42"}
    cp = asyncio.run(app.create_pull({"repo": "octocat/Hello-World", "title": "New feature", "head": "feat", "base": "main"}))
    assert cp["number"] == 42

    # close_pull
    app._req.return_value = {"number": 42, "state": "closed"}
    clp = asyncio.run(app.close_pull({"repo": "octocat/Hello-World", "number": 42, "state": "closed"}))
    assert clp["status"] == "closed"

