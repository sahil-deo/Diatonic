"""GitHub: repos, issues and pull requests over the REST API."""
from __future__ import annotations

import asyncio
import shutil

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..base import DESTRUCTIVE, READ, WRITE, App, Tool, ToolError

API = "https://api.github.com"


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


REPO = _str("owner/name (owner optional if a default is set)")
NUM = {"type": "integer", "description": "Issue/PR number"}


class GitHubApp(App):
    id = "github"
    name = "GitHub"
    icon = "git-branch"
    description = "Browse repositories, triage issues, review and merge pull requests."
    panel = True
    keywords = ["github", "gh", "repo", "repos", "repository", "repositories", "issue", "issues", "pull request",
                "pull", "pr", "prs", "merge", "branch", "commit", "diff", "readme"]
    settings = [
        {"key": "token", "label": "Personal access token", "secret": True,
         "help": "Needs 'repo' scope. Leave empty to reuse your `gh` CLI login.", "placeholder": "ghp_…"},
        {"key": "default_owner", "label": "Default owner", "secret": False,
         "help": "Lets you say 'my-repo' instead of 'owner/my-repo'.", "placeholder": "your-username"},
    ]

    # ---- auth + http -------------------------------------------------
    async def _token(self) -> str | None:
        tok = self.secret("token")
        if tok:
            return tok
        if shutil.which("gh"):
            try:
                p = await asyncio.create_subprocess_exec(
                    "gh", "auth", "token", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), 5)
                return out.decode().strip() or None
            except Exception:
                return None
        return None

    async def _req(self, method: str, path: str, **kw):
        tok = await self._token()
        if not tok:
            raise ToolError("GitHub isn't connected. Add a personal access token in Settings → Apps → GitHub.")
        headers = {"Authorization": f"Bearer {tok}", "Accept": kw.pop("accept", "application/vnd.github+json"),
                   "X-GitHub-Api-Version": "2022-11-28"}
        r = await self.ctx.http.request(method, API + path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise ToolError(f"GitHub {r.status_code}: {msg[:300]}")
        if "diff" in headers["Accept"]:
            return r.text
        return r.json() if r.content else {}

    async def _login(self) -> str | None:
        if not getattr(self, "_me", None):
            try:
                self._me = (await self._req("GET", "/user"))["login"]
            except ToolError:
                self._me = None
        return self._me

    async def _repo(self, repo: str) -> str:
        """'name' -> 'owner/name' using the default owner, else the signed-in user."""
        repo = repo.strip()
        if "/" not in repo:
            owner = self.setting("default_owner") or await self._login()
            if not owner:
                raise ToolError(f"'{repo}' needs an owner. Use 'owner/{repo}' or set a default owner in settings.")
            repo = f"{owner}/{repo}"
        return repo

    async def status(self) -> dict:
        try:
            me = await self._req("GET", "/user")
            return {"connected": True, "detail": f"Signed in as {me['login']}"}
        except ToolError as e:
            return {"connected": False, "detail": str(e)}

    # ---- shaping -----------------------------------------------------
    @staticmethod
    def _issue(i: dict) -> dict:
        repo = i.get("repository_url", "").replace(f"{API}/repos/", "")
        return {
            "repo": repo or None, "number": i["number"], "title": i["title"], "state": i["state"],
            "labels": [l["name"] for l in i.get("labels", [])],
            "assignees": [a["login"] for a in i.get("assignees", [])],
            "author": (i.get("user") or {}).get("login"), "comments": i.get("comments", 0),
            "updated_at": i.get("updated_at"), "url": i.get("html_url"),
            "is_pr": "pull_request" in i,
        }

    # ---- tools -------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("list_repos", "List the user's repositories, most recently updated first.",
                 {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}}, self.list_repos),
            Tool("get_repo", "Get one repository's details: description, homepage, topics, default branch, last push.",
                 {"properties": {"repo": REPO}, "required": ["repo"]}, self.get_repo),
            Tool("update_repo", "Change a repository's description or homepage URL.",
                 {"properties": {"repo": REPO, "description": _str("New description"), "homepage": _str("New homepage URL")},
                  "required": ["repo"]}, self.update_repo, WRITE,
                 describe=lambda a: f"Update {a.get('repo')}: " + ", ".join(
                     f"{k} → “{v}”" for k, v in a.items() if k in ("description", "homepage"))),
            Tool("list_issues", "List issues in a repository (pull requests excluded).",
                 {"properties": {"repo": REPO, "state": {"type": "string", "enum": ["open", "closed", "all"]},
                                 "labels": _str("Comma-separated label names"),
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                  "required": ["repo"]}, self.list_issues),
            Tool("search_issues", "Search issues/PRs with GitHub syntax, e.g. 'is:open is:issue assignee:@me'.",
                 {"properties": {"query": _str("Search query"),
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 30}},
                  "required": ["query"]}, self.search_issues),
            Tool("get_issue", "Get one issue with its body and recent comments.",
                 {"properties": {"repo": REPO, "number": NUM}, "required": ["repo", "number"]}, self.get_issue),
            Tool("create_issue", "Create a new issue.",
                 {"properties": {"repo": REPO, "title": _str("Issue title"), "body": _str("Markdown body"),
                                 "labels": {"type": "array", "items": {"type": "string"}}},
                  "required": ["repo", "title"]}, self.create_issue, WRITE,
                 describe=lambda a: f"Create issue “{a.get('title')}” in {a.get('repo')}"),
            Tool("comment_issue", "Add a comment to an issue or pull request.",
                 {"properties": {"repo": REPO, "number": NUM, "body": _str("Comment text (Markdown)")},
                  "required": ["repo", "number", "body"]}, self.comment, WRITE,
                 describe=lambda a: f"Comment on {a.get('repo')}#{a.get('number')}: “{str(a.get('body'))[:80]}”"),
            Tool("close_issue", "Close an issue.",
                 {"properties": {"repo": REPO, "number": NUM,
                                 "reason": {"type": "string", "enum": ["completed", "not_planned"]}},
                  "required": ["repo", "number"]}, self.close_issue, WRITE,
                 describe=lambda a: f"Close issue {a.get('repo')}#{a.get('number')}"),
            Tool("list_pulls", "List pull requests in a repository.",
                 {"properties": {"repo": REPO, "state": {"type": "string", "enum": ["open", "closed", "all"]},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                  "required": ["repo"]}, self.list_pulls),
            Tool("get_pull", "Get a pull request: status, mergeability, changed files and CI state.",
                 {"properties": {"repo": REPO, "number": NUM}, "required": ["repo", "number"]}, self.get_pull),
            Tool("get_pull_diff", "Get the (truncated) unified diff of a pull request.",
                 {"properties": {"repo": REPO, "number": NUM}, "required": ["repo", "number"]}, self.get_pull_diff),
            Tool("merge_pull", "Merge a pull request.",
                 {"properties": {"repo": REPO, "number": NUM,
                                 "method": {"type": "string", "enum": ["merge", "squash", "rebase"]}},
                  "required": ["repo", "number"]}, self.merge_pull, DESTRUCTIVE,
                 describe=lambda a: f"Merge PR {a.get('repo')}#{a.get('number')} ({a.get('method', 'squash')})"),
        ]

    async def list_repos(self, a):
        data = await self._req("GET", "/user/repos", params={"sort": "updated", "per_page": a.get("limit", 20)})
        return [{"name": r["full_name"], "description": r["description"], "private": r["private"],
                 "open_issues": r["open_issues_count"], "updated_at": r["pushed_at"]} for r in data]

    async def get_repo(self, a):
        r = await self._req("GET", f"/repos/{await self._repo(a['repo'])}")
        return {"name": r["full_name"], "description": r["description"], "homepage": r["homepage"],
                "topics": r.get("topics"), "private": r["private"], "default_branch": r["default_branch"],
                "open_issues": r["open_issues_count"], "stars": r["stargazers_count"], "pushed_at": r["pushed_at"],
                "url": r["html_url"]}

    async def update_repo(self, a):
        body = {k: a[k] for k in ("description", "homepage") if k in a}
        if not body:
            raise ToolError("Nothing to change: pass a description and/or homepage.")
        repo = await self._repo(a["repo"])
        r = await self._req("PATCH", f"/repos/{repo}", json=body)
        return {"updated": r["full_name"], "description": r["description"], "homepage": r["homepage"]}

    async def list_issues(self, a):
        p = {"state": a.get("state", "open"), "per_page": a.get("limit", 20)}
        if a.get("labels"):
            p["labels"] = a["labels"]
        data = await self._req("GET", f"/repos/{await self._repo(a['repo'])}/issues", params=p)
        return [self._issue(i) for i in data if "pull_request" not in i]

    async def search_issues(self, a):
        data = await self._req("GET", "/search/issues", params={"q": a["query"], "per_page": a.get("limit", 15)})
        return {"total": data["total_count"], "items": [self._issue(i) for i in data["items"]]}

    async def get_issue(self, a):
        repo = await self._repo(a["repo"])
        i = await self._req("GET", f"/repos/{repo}/issues/{a['number']}")
        comments = await self._req("GET", f"/repos/{repo}/issues/{a['number']}/comments", params={"per_page": 5})
        out = self._issue(i) | {"repo": repo, "body": (i.get("body") or "")[:3000]}
        out["recent_comments"] = [{"author": c["user"]["login"], "body": c["body"][:600]} for c in comments]
        return out

    async def create_issue(self, a):
        repo = await self._repo(a["repo"])
        i = await self._req("POST", f"/repos/{repo}/issues", json={
            "title": a["title"], "body": a.get("body", ""), "labels": a.get("labels", [])})
        return {"created": f"{repo}#{i['number']}", "url": i["html_url"]}

    async def comment(self, a):
        repo = await self._repo(a["repo"])
        c = await self._req("POST", f"/repos/{repo}/issues/{a['number']}/comments", json={"body": a["body"]})
        return {"commented": f"{repo}#{a['number']}", "url": c["html_url"]}

    async def close_issue(self, a):
        repo = await self._repo(a["repo"])
        await self._req("PATCH", f"/repos/{repo}/issues/{a['number']}",
                        json={"state": "closed", "state_reason": a.get("reason", "completed")})
        return {"closed": f"{repo}#{a['number']}"}

    async def list_pulls(self, a):
        data = await self._req("GET", f"/repos/{await self._repo(a['repo'])}/pulls",
                               params={"state": a.get("state", "open"), "per_page": a.get("limit", 20)})
        return [{"number": p["number"], "title": p["title"], "state": p["state"], "draft": p["draft"],
                 "author": p["user"]["login"], "branch": p["head"]["ref"], "updated_at": p["updated_at"],
                 "url": p["html_url"]} for p in data]

    async def get_pull(self, a):
        repo = await self._repo(a["repo"])
        p = await self._req("GET", f"/repos/{repo}/pulls/{a['number']}")
        files = await self._req("GET", f"/repos/{repo}/pulls/{a['number']}/files", params={"per_page": 30})
        checks = {}
        try:
            cr = await self._req("GET", f"/repos/{repo}/commits/{p['head']['sha']}/check-runs",
                                 params={"per_page": 30})
            for c in cr.get("check_runs", []):
                checks[c["conclusion"] or c["status"]] = checks.get(c["conclusion"] or c["status"], 0) + 1
        except ToolError:
            pass
        return {"repo": repo, "number": p["number"], "title": p["title"], "state": p["state"],
                "merged": p["merged"], "draft": p["draft"], "mergeable": p.get("mergeable"),
                "mergeable_state": p.get("mergeable_state"), "author": p["user"]["login"],
                "base": p["base"]["ref"], "head": p["head"]["ref"], "body": (p.get("body") or "")[:1500],
                "additions": p["additions"], "deletions": p["deletions"], "checks": checks,
                "files": [{"name": f["filename"], "+": f["additions"], "-": f["deletions"]} for f in files],
                "url": p["html_url"]}

    async def get_pull_diff(self, a):
        d = await self._req("GET", f"/repos/{await self._repo(a['repo'])}/pulls/{a['number']}",
                            accept="application/vnd.github.diff")
        return d[:8000] + ("\n… [diff truncated]" if len(d) > 8000 else "")

    async def merge_pull(self, a):
        repo = await self._repo(a["repo"])
        r = await self._req("PUT", f"/repos/{repo}/pulls/{a['number']}/merge",
                            json={"merge_method": a.get("method", "squash")})
        return {"merged": r.get("merged", False), "message": r.get("message")}

    # ---- panel route -------------------------------------------------
    async def panel_data(self, request: Request):
        try:
            issues = await self._req("GET", "/search/issues", params={
                "q": "is:issue is:open assignee:@me archived:false", "per_page": 15, "sort": "updated"})
            prs = await self._req("GET", "/search/issues", params={
                "q": "is:pr is:open involves:@me archived:false", "per_page": 15, "sort": "updated"})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, status_code=200)
        return JSONResponse({"issues": [self._issue(i) for i in issues["items"]],
                             "prs": [self._issue(i) for i in prs["items"]]})

    def routes(self):
        return [Route("/panel", self.panel_data)]
