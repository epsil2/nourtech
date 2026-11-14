"""GitHub read-only client (Sprint 3 scope: visibility, never a write).

The token is read from the environment (``GITHUB_TOKEN``) and never leaves the
process: routes return repository facts, not credentials. ``opener`` is
injectable so the whole module is testable offline.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API = "https://api.github.com"


class GitHubNotConfigured(RuntimeError):
    """No token: the deployment simply has no GitHub integration."""


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"github {status}: {message}")
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        repo: str | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN", "")
        self.repo = (repo or os.getenv("GITHUB_REPO", "")).strip("/")
        self._opener = opener or urllib.request.urlopen

    @property
    def token_present(self) -> bool:
        return bool(self._token)

    @property
    def configured(self) -> bool:
        return bool(self._token and self.repo)

    def _require(self) -> None:
        if not self.configured:
            raise GitHubNotConfigured("GITHUB_TOKEN and GITHUB_REPO must be set")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._require()
        url = path if path.startswith("https://") else f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-agent-factory",
            },
        )
        try:
            with self._opener(request, timeout=15) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode()[:200] if exc.fp else str(exc)
            raise GitHubError(exc.code, detail) from exc

    # -- reads -----------------------------------------------------------
    def repo_info(self) -> dict[str, Any]:
        body = self.get(f"/repos/{self.repo}")
        return {
            "full_name": body.get("full_name"),
            "private": bool(body.get("private")),
            "default_branch": body.get("default_branch"),
            "html_url": body.get("html_url"),
            "pushed_at": body.get("pushed_at"),
            "open_issues": body.get("open_issues_count"),
            "permissions": body.get("permissions") or {},
        }

    def pulls(self, state: str = "all", limit: int = 10) -> list[dict[str, Any]]:
        rows = self.get(f"/repos/{self.repo}/pulls", {"state": state, "per_page": min(limit, 50)})
        return [
            {
                "number": p.get("number"),
                "title": (p.get("title") or "")[:160],
                "state": p.get("state"),
                "draft": bool(p.get("draft")),
                "merged": bool(p.get("merged_at")),
                "author": (p.get("user") or {}).get("login"),
                "branch": (p.get("head") or {}).get("ref"),
                "created_at": p.get("created_at"),
                "html_url": p.get("html_url"),
            }
            for p in rows
        ]

    def commits(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.get(f"/repos/{self.repo}/commits", {"per_page": min(limit, 50)})
        return [
            {
                "sha": (c.get("sha") or "")[:7],
                "message": ((c.get("commit") or {}).get("message") or "").splitlines()[0][:160],
                "author": ((c.get("commit") or {}).get("author") or {}).get("name"),
                "date": ((c.get("commit") or {}).get("author") or {}).get("date"),
            }
            for c in rows
        ]

    def checks(self, sha: str) -> list[dict[str, Any]]:
        body = self.get(f"/repos/{self.repo}/commits/{sha}/check-runs")
        return [
            {
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
            }
            for r in body.get("check_runs", []) or []
        ]

    def workflow_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        body = self.get(f"/repos/{self.repo}/actions/runs", {"per_page": min(limit, 50)})
        return [
            {
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "branch": r.get("head_branch"),
                "event": r.get("event"),
                "created_at": r.get("created_at"),
                "html_url": r.get("html_url"),
            }
            for r in body.get("workflow_runs", []) or []
        ]

    def releases(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.get(f"/repos/{self.repo}/releases", {"per_page": min(limit, 20)})
        return [
            {
                "tag": r.get("tag_name"),
                "name": r.get("name"),
                "draft": bool(r.get("draft")),
                "prerelease": bool(r.get("prerelease")),
                "published_at": r.get("published_at"),
                "html_url": r.get("html_url"),
            }
            for r in rows
        ]
