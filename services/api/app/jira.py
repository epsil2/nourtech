"""Read-only Jira discovery (SCRUM-24, SCRUM-36).

The Factory needs to know which Jira project, issue types, fields and sprints it
is working with. It never writes: this module only issues ``GET`` requests, and
the classes it exposes have no method that could mutate the project. That is a
deliberate constraint — the team's Jira configuration is not ours to change.

The API token is read from the environment and used only to build the
``Authorization`` header; it is never returned, logged, or stored.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

TIMEOUT = 15


class JiraNotConfigured(RuntimeError):
    pass


class JiraError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"jira {status}: {message}")
        self.status = status


class JiraClient:
    """Minimal read-only client. ``opener`` is injectable for tests."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        token: str | None = None,
        opener: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("JIRA_SITE_URL", "")).rstrip("/")
        self.email = email or os.getenv("JIRA_ACCOUNT_EMAIL", "")
        self._token = token or os.getenv("JIRA_API_TOKEN", "")
        self._opener = opener or urllib.request.urlopen

    # -- configuration ---------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self._token)

    def _require(self) -> None:
        if not self.configured:
            raise JiraNotConfigured(
                "JIRA_SITE_URL, JIRA_ACCOUNT_EMAIL and JIRA_API_TOKEN must be set"
            )

    # -- transport -------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require()
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        raw = f"{self.email}:{self._token}".encode()
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:  # 401/403/404 are expected outcomes
            raise JiraError(exc.code, exc.reason) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JiraError(0, str(exc)) from exc

    # -- read-only surfaces ---------------------------------------------
    def myself(self) -> dict[str, Any]:
        me = self._get("/rest/api/3/myself")
        return {
            "account_id": me.get("accountId"),
            "display_name": me.get("displayName"),
            "time_zone": me.get("timeZone"),
        }

    def projects(self) -> list[dict[str, Any]]:
        body = self._get("/rest/api/3/project/search", {"maxResults": 50})
        return [
            {"key": p.get("key"), "name": p.get("name"), "style": p.get("style")}
            for p in body.get("values", [])
        ]

    def project(self, key: str) -> dict[str, Any]:
        body = self._get(f"/rest/api/3/project/{urllib.parse.quote(key)}")
        statuses = []
        for issue_type in body.get("issueTypes", []) or []:
            statuses.extend(s.get("name") for s in issue_type.get("statuses", []) or [])
        return {
            "key": body.get("key"),
            "name": body.get("name"),
            "issue_types": [t.get("name") for t in body.get("issueTypes", []) or []],
            "statuses": sorted({s for s in statuses if s}),
        }

    def sprints(self, board_id: int) -> list[dict[str, Any]]:
        body = self._get(f"/rest/agile/1.0/board/{board_id}/sprint", {"maxResults": 50})
        return [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "state": s.get("state"),
                "start": (s.get("startDate") or "")[:10],
                "end": (s.get("endDate") or "")[:10],
            }
            for s in body.get("values", [])
        ]

    def issues(self, jql: str, limit: int = 100) -> list[dict[str, Any]]:
        body = self._get(
            "/rest/api/3/search/jql",
            {
                "jql": jql,
                "maxResults": min(limit, 100),
                "fields": "summary,status,issuetype,labels,parent",
            },
        )
        out = []
        for issue in body.get("issues", []):
            fields = issue.get("fields") or {}
            out.append(
                {
                    "key": issue.get("key"),
                    "summary": (fields.get("summary") or "")[:200],
                    "type": (fields.get("issuetype") or {}).get("name"),
                    "status": (fields.get("status") or {}).get("name"),
                    "labels": fields.get("labels") or [],
                }
            )
        return out

    def transitions(self, key: str) -> list[dict[str, Any]]:
        """Workflow transitions available on one issue (read-only lookup)."""
        body = self._get(f"/rest/api/3/issue/{urllib.parse.quote(key)}/transitions")
        return [
            {"id": t.get("id"), "name": t.get("name"), "to": (t.get("to") or {}).get("name")}
            for t in body.get("transitions", []) or []
        ]

    def fields(self, limit: int = 200) -> list[dict[str, Any]]:
        body = self._get("/rest/api/3/field")
        return [
            {"id": f.get("id"), "name": f.get("name"), "custom": bool(f.get("custom"))}
            for f in body[:limit]
        ]

    def schema(self, project_key: str, board_id: int | None = None) -> dict[str, Any]:
        """One call that answers 'what can this Factory work with?'"""
        me = self.myself()
        project = self.project(project_key)
        sprints = self.sprints(board_id) if board_id else []
        return {
            "read_only": True,
            "site": self.base_url,
            "account": me,
            "project": project,
            "fields_total": len(self.fields()),
            "sprints": sprints,
        }


def health() -> dict[str, Any]:
    """Cheap readiness probe: is the integration configured at all?"""
    client = JiraClient()
    return {
        "configured": client.configured,
        "site": client.base_url or None,
        "read_only": True,
    }
