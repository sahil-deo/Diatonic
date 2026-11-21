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
    private = True   # a cloud model must ask before reading or changing it
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
            raise ToolError(f"GitHub {r.status_code}: {msg[:300]}", bad_args=r.status_code in (400, 422))
        if "diff" in headers["Accept"] or "raw" in headers["Accept"]:
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
        except (ToolError, Exception) as e:
            return {"connected": False, "detail": str(e)}

    async def snapshot(self, n: int) -> dict:
        data = await self._req("GET", "/search/issues", params={"q": "is:open assignee:@me archived:false", "per_page": n, "sort": "updated"})
        items = [self._issue(i) for i in data["items"]]
        return {"items": [{"text": i["title"], "sub": f"{(i['repo'] or '').split('/')[-1]}#{i['number']}", "badge": "PR" if i["is_pr"] else None,
                           "url": i["url"]} for i in items],
                "more": f"{data['total_count']} open assigned to you" if data["total_count"] > n else None,
                "empty": "Nothing assigned to you."}

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
                 {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}}, self.list_repos),
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
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                  "required": ["repo"]}, self.list_issues),
            Tool("search_issues", "Search issues/PRs with GitHub syntax, e.g. 'is:open is:issue assignee:@me'.",
                 {"properties": {"query": _str("Search query"),
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
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
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
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
            Tool("create_pull", "Create a new pull request.",
                 {"properties": {"repo": REPO, "title": _str("Pull request title"),
                                 "head": _str("The branch containing the changes"),
                                 "base": _str("The branch to pull changes into"),
                                 "body": _str("Pull request description"),
                                 "draft": {"type": "boolean", "description": "Create as draft PR"}},
                  "required": ["repo", "title", "head", "base"]}, self.create_pull, WRITE,
                 describe=lambda a: f"Create PR “{a.get('title')}” in {a.get('repo')} ({a.get('head')} → {a.get('base')})"),
            Tool("close_pull", "Close or reopen a pull request.",
                 {"properties": {"repo": REPO, "number": NUM,
                                 "state": {"type": "string", "enum": ["closed", "open"]}},
                  "required": ["repo", "number"]}, self.close_pull, WRITE,
                 describe=lambda a: f"{'Reopen' if a.get('state') == 'open' else 'Close'} PR {a.get('repo')}#{a.get('number')}"),
            Tool("list_branches", "List branches in a repository.",
                 {"properties": {"repo": REPO, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
                  "required": ["repo"]}, self.list_branches),
            Tool("create_branch", "Create a new branch in a repository.",
                 {"properties": {"repo": REPO, "branch": _str("New branch name"),
                                 "from_branch": _str("Base branch or commit SHA (defaults to default branch)")},
                  "required": ["repo", "branch"]}, self.create_branch, WRITE,
                 describe=lambda a: f"Create branch {a.get('branch')} in {a.get('repo')}"),
            Tool("delete_branch", "Delete a branch from a repository.",
                 {"properties": {"repo": REPO, "branch": _str("Branch name to delete")},
                  "required": ["repo", "branch"]}, self.delete_branch, DESTRUCTIVE,
                 describe=lambda a: f"Delete branch {a.get('branch')} in {a.get('repo')}"),
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

    async def create_pull(self, a):
        repo = await self._repo(a["repo"])
        payload = {
            "title": a["title"],
            "head": a["head"],
            "base": a["base"],
            "body": a.get("body", ""),
        }
        if "draft" in a:
            payload["draft"] = a["draft"]
        p = await self._req("POST", f"/repos/{repo}/pulls", json=payload)
        return {"created": f"{repo}#{p['number']}", "number": p["number"], "url": p["html_url"]}

    async def close_pull(self, a):
        repo = await self._repo(a["repo"])
        st = a.get("state", "closed")
        p = await self._req("PATCH", f"/repos/{repo}/pulls/{a['number']}", json={"state": st})
        return {"status": p["state"], "number": p["number"], "repo": repo}

    async def list_branches(self, a):
        repo = await self._repo(a["repo"])
        data = await self._req("GET", f"/repos/{repo}/branches", params={"per_page": a.get("limit", 30)})
        return [{"name": b["name"], "protected": b.get("protected", False), "commit_sha": b.get("commit", {}).get("sha")} for b in data]

    async def create_branch(self, a):
        repo = await self._repo(a["repo"])
        from_ref = a.get("from_branch")
        if not from_ref:
            r = await self._req("GET", f"/repos/{repo}")
            from_ref = r["default_branch"]
        if len(from_ref) != 40 or not all(c in "0123456789abcdefABCDEF" for c in from_ref):
            ref_data = await self._req("GET", f"/repos/{repo}/git/ref/heads/{from_ref}")
            sha = ref_data["object"]["sha"]
        else:
            sha = from_ref
        res = await self._req("POST", f"/repos/{repo}/git/refs", json={
            "ref": f"refs/heads/{a['branch']}",
            "sha": sha
        })
        return {"created": a["branch"], "ref": res.get("ref"), "sha": sha}

    async def delete_branch(self, a):
        repo = await self._repo(a["repo"])
        branch = a["branch"]
        await self._req("DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        return {"deleted": branch, "repo": repo}

    # ---- panel routes -------------------------------------------------
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

    async def route_repos(self, request: Request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 100)
            repos = await self.list_repos({"limit": limit})
            return JSONResponse({"repos": repos})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_repo(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        repo_slug = f"{owner}/{name}"
        try:
            repo_info = await self.get_repo({"repo": repo_slug})
            return JSONResponse(repo_info)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_readme(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        repo_slug = f"{owner}/{name}"
        try:
            content = await self._req("GET", f"/repos/{repo_slug}/readme", accept="application/vnd.github.raw+json")
            return JSONResponse({"readme": content if isinstance(content, str) else str(content)})
        except ToolError as e:
            return JSONResponse({"error": str(e), "readme": ""}, 200)

    async def route_branches(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        repo_slug = f"{owner}/{name}"
        if request.method == "POST":
            try:
                body = await request.json()
                res = await self.create_branch({
                    "repo": repo_slug,
                    "branch": body["branch"],
                    "from_branch": body.get("from_branch")
                })
                return JSONResponse(res)
            except ToolError as e:
                return JSONResponse({"error": str(e)}, 400)
        try:
            branches = await self.list_branches({"repo": repo_slug, "limit": 100})
            return JSONResponse({"branches": branches})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_branch_delete(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        branch = request.path_params["branch"]
        repo_slug = f"{owner}/{name}"
        try:
            res = await self.delete_branch({"repo": repo_slug, "branch": branch})
            return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pulls(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        repo_slug = f"{owner}/{name}"
        if request.method == "POST":
            try:
                body = await request.json()
                res = await self.create_pull({
                    "repo": repo_slug,
                    "title": body["title"],
                    "head": body["head"],
                    "base": body["base"],
                    "body": body.get("body", ""),
                    "draft": body.get("draft", False)
                })
                return JSONResponse(res)
            except ToolError as e:
                return JSONResponse({"error": str(e)}, 400)
        try:
            state = request.query_params.get("state", "open")
            prs = await self.list_pulls({"repo": repo_slug, "state": state, "limit": 50})
            return JSONResponse({"pulls": prs})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pull(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            pull = await self.get_pull({"repo": repo_slug, "number": number})
            comments = await self._req("GET", f"/repos/{repo_slug}/issues/{number}/comments", params={"per_page": 50})
            pull["comments"] = [{
                "id": c["id"],
                "author": c["user"]["login"],
                "author_avatar": c["user"].get("avatar_url"),
                "body": c["body"],
                "created_at": c["created_at"]
            } for c in comments]
            return JSONResponse(pull)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pull_diff(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            diff = await self._req("GET", f"/repos/{repo_slug}/pulls/{number}",
                                   accept="application/vnd.github.diff")
            return JSONResponse({"diff": diff})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pull_merge(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            body = await request.json() if request.headers.get("content-type") == "application/json" else {}
            res = await self.merge_pull({
                "repo": repo_slug,
                "number": number,
                "method": body.get("method", "squash")
            })
            return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pull_close(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            body = await request.json() if request.headers.get("content-type") == "application/json" else {}
            res = await self.close_pull({
                "repo": repo_slug,
                "number": number,
                "state": body.get("state", "closed")
            })
            return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_pull_comment(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            body = await request.json()
            res = await self.comment({"repo": repo_slug, "number": number, "body": body["body"]})
            return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_issues(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        repo_slug = f"{owner}/{name}"
        if request.method == "POST":
            try:
                body = await request.json()
                res = await self.create_issue({
                    "repo": repo_slug,
                    "title": body["title"],
                    "body": body.get("body", ""),
                    "labels": body.get("labels", [])
                })
                return JSONResponse(res)
            except ToolError as e:
                return JSONResponse({"error": str(e)}, 400)
        try:
            state = request.query_params.get("state", "open")
            issues = await self.list_issues({"repo": repo_slug, "state": state, "limit": 50})
            return JSONResponse({"issues": issues})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_issue(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            issue = await self.get_issue({"repo": repo_slug, "number": number})
            comments = await self._req("GET", f"/repos/{repo_slug}/issues/{number}/comments", params={"per_page": 50})
            issue["comments"] = [{
                "id": c["id"],
                "author": c["user"]["login"],
                "author_avatar": c["user"].get("avatar_url"),
                "body": c["body"],
                "created_at": c["created_at"]
            } for c in comments]
            return JSONResponse(issue)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_issue_close(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            body = await request.json() if request.headers.get("content-type") == "application/json" else {}
            state = body.get("state", "closed")
            if state == "open":
                await self._req("PATCH", f"/repos/{repo_slug}/issues/{number}", json={"state": "open"})
                return JSONResponse({"opened": f"{repo_slug}#{number}"})
            else:
                res = await self.close_issue({
                    "repo": repo_slug,
                    "number": number,
                    "reason": body.get("reason", "completed")
                })
                return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def route_issue_comment(self, request: Request):
        owner = request.path_params["owner"]
        name = request.path_params["name"]
        number = request.path_params["number"]
        repo_slug = f"{owner}/{name}"
        try:
            body = await request.json()
            res = await self.comment({"repo": repo_slug, "number": number, "body": body["body"]})
            return JSONResponse(res)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    def routes(self):
        return [
            Route("/panel", self.panel_data),
            Route("/repos", self.route_repos, methods=["GET"]),
            Route("/repo/{owner}/{name}", self.route_repo, methods=["GET"]),
            Route("/repo/{owner}/{name}/readme", self.route_readme, methods=["GET"]),
            Route("/repo/{owner}/{name}/branches", self.route_branches, methods=["GET", "POST"]),
            Route("/repo/{owner}/{name}/branches/{branch:path}", self.route_branch_delete, methods=["DELETE"]),
            Route("/repo/{owner}/{name}/pulls", self.route_pulls, methods=["GET", "POST"]),
            Route("/repo/{owner}/{name}/pulls/{number:int}", self.route_pull, methods=["GET"]),
            Route("/repo/{owner}/{name}/pulls/{number:int}/diff", self.route_pull_diff, methods=["GET"]),
            Route("/repo/{owner}/{name}/pulls/{number:int}/merge", self.route_pull_merge, methods=["POST"]),
            Route("/repo/{owner}/{name}/pulls/{number:int}/close", self.route_pull_close, methods=["POST", "PATCH"]),
            Route("/repo/{owner}/{name}/pulls/{number:int}/comment", self.route_pull_comment, methods=["POST"]),
            Route("/repo/{owner}/{name}/issues", self.route_issues, methods=["GET", "POST"]),
            Route("/repo/{owner}/{name}/issues/{number:int}", self.route_issue, methods=["GET"]),
            Route("/repo/{owner}/{name}/issues/{number:int}/close", self.route_issue_close, methods=["POST", "PATCH"]),
            Route("/repo/{owner}/{name}/issues/{number:int}/comment", self.route_issue_comment, methods=["POST"]),
        ]
