"""Guarded write surface for Jira (SCRUM-45).

Reads stay in :mod:`app.jira`, which still cannot mutate anything: this module is
the only place that issues a mutating HTTP verb, and it is behind three locks.

1. ``JIRA_WRITE_ENABLED`` must be exactly true. Absent or false → every write
   raises :class:`JiraWriteDisabled` (HTTP 403 from the API). Default is off, so
   deploying the code changes nothing until someone opts in.
2. Writes only touch the projects listed in ``JIRA_WRITE_PROJECT`` (default
   ``SCRUM``). Both the target project and the ``KEY-123`` prefix of an issue are
   checked, so a PR coming from anywhere cannot write into another project.
3. Every successful write is returned with the Jira URL and must be audited by
   the caller with the actor that asked for it.

Creation is idempotent: issues are labelled ``ai-factory`` and a creation whose
summary already exists on that label returns the existing key (``created`` false)
instead of duplicating the backlog on a retry.

The API token is read from the environment and never returned or logged.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .jira import JiraClient, JiraError, JiraNotConfigured

TIMEOUT = 15
MARKER_LABEL = "ai-factory"
WRITE_FLAG = "JIRA_WRITE_ENABLED"
PROJECTS_ENV = "JIRA_WRITE_PROJECT"


class JiraWriteDisabled(RuntimeError):
    """Writes are not opted in on this deployment."""


class JiraWriteForbidden(RuntimeError):
    """The write targets a project outside the allowlist."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _adf(text: str) -> dict[str, Any]:
    """Minimal Atlassian Document Format document (Jira Cloud rejects plain text)."""
    paragraphs = [line for line in (text or "").split("\n")]
    content = []
    for line in paragraphs:
        if line.strip():
            content.append({"type": "paragraph", "content": [{"type": "text", "text": line[:2000]}]})
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": "-"}]}]
    return {"type": "doc", "version": 1, "content": content}


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


class JiraWriter:
    """Opt-in, allowlisted, idempotent Jira writes. ``opener`` is injectable."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        token: str | None = None,
        opener: Callable[[urllib.request.Request], Any] | None = None,
        enabled: bool | None = None,
        projects: list[str] | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("JIRA_SITE_URL", "")).rstrip("/")
        self.email = email or os.getenv("JIRA_ACCOUNT_EMAIL", "")
        self._token = token or os.getenv("JIRA_API_TOKEN", "")
        self._opener = opener or urllib.request.urlopen
        self._enabled = _truthy(os.getenv(WRITE_FLAG)) if enabled is None else enabled
        raw = os.getenv(PROJECTS_ENV, "SCRUM") if projects is None else ",".join(projects)
        self.projects = [p.strip().upper() for p in raw.split(",") if p.strip()]

    # -- locks -----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.email and self._token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise JiraWriteDisabled(
                f"jira writes are disabled: set {WRITE_FLAG}=true and {PROJECTS_ENV} to opt in"
            )
        if not self.configured:
            raise JiraNotConfigured(
                "JIRA_SITE_URL, JIRA_ACCOUNT_EMAIL and JIRA_API_TOKEN must be set"
            )
        if not self.projects:
            raise JiraWriteDisabled(f"{PROJECTS_ENV} lists no project")

    def check_project(self, value: str) -> str:
        """Accept a project key or an issue key; reject anything outside the allowlist."""
        key = (value or "").strip().upper()
        project = key.split("-", 1)[0]
        if project not in self.projects:
            raise JiraWriteForbidden(
                f"project {project or value!r} is outside the write allowlist {self.projects}"
            )
        return key

    # -- transport -------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        raw = f"{self.email}:{self._token}".encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=TIMEOUT) as response:
                body = response.read().decode()
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode()).get("errorMessages") or ""
            except Exception:  # noqa: BLE001 - best effort, the status is what matters
                detail = ""
            raise JiraError(exc.code, f"{exc.reason} {detail}".strip()) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JiraError(0, str(exc)) from exc

    # -- writes ----------------------------------------------------------
    def create_issue(
        self,
        summary: str,
        issue_type: str = "Story",
        description: str | None = None,
        project: str | None = None,
        labels: list[str] | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        self._require_enabled()
        project = self.check_project(project or self.projects[0])
        summary = " ".join((summary or "").split())[:180]
        if len(summary) < 3:
            raise ValueError("summary must be at least 3 characters")

        reader = JiraClient(self.base_url, self.email, self._token, self._opener)
        existing = [
            row
            for row in reader.issues(f'project = "{project}" AND labels = "{MARKER_LABEL}"', 100)
            if _norm(row.get("summary", "")) == _norm(summary)
        ]
        if existing:
            key = existing[0]["key"]
            return {"key": key, "id": None, "created": False,
                    "url": f"{self.base_url}/browse/{key}"}

        fields: dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "issuetype": {"name": issue_type},
            "labels": [MARKER_LABEL, *(labels or [])],
        }
        if description:
            fields["description"] = _adf(description)
        if parent:
            fields["parent"] = {"key": self.check_project(parent)}
        body = self._post("/rest/api/3/issue", {"fields": fields})
        key = body.get("key", "")
        return {"key": key, "id": body.get("id"), "created": True,
                "url": f"{self.base_url}/browse/{key}"}

    def comment(self, key: str, text: str) -> dict[str, Any]:
        self._require_enabled()
        key = self.check_project(key)
        body = self._post(
            f"/rest/api/3/issue/{urllib.parse.quote(key)}/comment", {"body": _adf(text)}
        )
        return {"key": key, "comment_id": body.get("id"), "url": f"{self.base_url}/browse/{key}"}

    def transition(self, key: str, target: str) -> dict[str, Any]:
        self._require_enabled()
        key = self.check_project(key)
        reader = JiraClient(self.base_url, self.email, self._token, self._opener)
        available = reader.transitions(key)
        wanted = (target or "").strip().casefold()
        match = next(
            (
                t
                for t in available
                if wanted in {(str(t.get("id")) or "").casefold(), (t.get("name") or "").casefold(),
                              (t.get("to") or "").casefold()}
            ),
            None,
        )
        if not match:
            raise ValueError(
                f"no transition {target!r} on {key}; available: "
                + ", ".join(sorted({str(t.get("name")) for t in available}))
            )
        self._post(
            f"/rest/api/3/issue/{urllib.parse.quote(key)}/transitions",
            {"transition": {"id": str(match["id"])}},
        )
        return {"key": key, "transition": match.get("name"), "to": match.get("to"),
                "url": f"{self.base_url}/browse/{key}"}


def status() -> dict[str, Any]:
    """What the API may do with Jira on this deployment."""
    writer = JiraWriter()
    return {
        "configured": writer.configured,
        "site": writer.base_url or None,
        "write_enabled": writer.enabled,
        "write_projects": writer.projects,
        "read_only": not writer.enabled,
    }
