"""Read-only Jira integration (SCRUM-24, SCRUM-36).

The important property is not that the calls work: it is that nothing in this
integration *can* write to a Jira project. The tests below pin that down both at
the HTTP level (only GET is ever issued) and at the source level (no mutating
verb appears in the module).
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.jira import JiraClient, JiraError, JiraNotConfigured  # noqa: E402

BAD_TOKEN = "ATA" + "TT" + "x" * 24


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def make_opener(routes: dict[str, dict], seen: list[tuple[str, str]]):
    def opener(request, timeout=None):
        seen.append((request.get_method(), request.full_url))
        for fragment, payload in routes.items():
            if fragment in request.full_url:
                return FakeResponse(json.dumps(payload).encode())
        return FakeResponse(b"{}")

    return opener


def client(routes, seen):
    return JiraClient(
        base_url="https://example.atlassian.net",
        email="user@example.com",
        token=BAD_TOKEN,
        opener=make_opener(routes, seen),
    )


def test_integration_is_not_configured_without_credentials(monkeypatch):
    for var in ("JIRA_SITE_URL", "JIRA_ACCOUNT_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert JiraClient().configured is False
    with pytest.raises(JiraNotConfigured):
        JiraClient().myself()


def test_only_get_requests_are_issued():
    seen: list[tuple[str, str]] = []
    c = client(
        {
            "/myself": {"accountId": "1", "displayName": "N", "timeZone": "Europe/Paris"},
            "/project/SCRUM": {"key": "SCRUM", "name": "Ai Factory", "issueTypes": [{"name": "Story", "statuses": [{"name": "To Do"}]}]},
            "/field": [{"id": "summary", "name": "Summary"}],
            "/board/1/sprint": {"values": [{"id": 2, "name": "Sprint 2", "state": "active", "startDate": "2026-11-01T00:00:00Z"}]},
            "/search/jql": {"issues": [{"key": "SCRUM-17", "fields": {"summary": "s", "issuetype": {"name": "Story"}, "status": {"name": "In Review"}, "labels": []}}]},
        },
        seen,
    )
    schema = c.schema("SCRUM", board_id=1)
    assert schema["read_only"] is True
    assert schema["project"]["key"] == "SCRUM"
    assert schema["sprints"][0]["name"] == "Sprint 2"
    assert c.issues("project=SCRUM", 10)[0]["key"] == "SCRUM-17"
    assert {method for method, _ in seen} == {"GET"}


def test_token_never_leaves_the_client():
    seen: list[tuple[str, str]] = []
    c = client({"/myself": {"accountId": "1", "displayName": "N"}}, seen)
    assert BAD_TOKEN not in json.dumps(c.myself())
    assert BAD_TOKEN not in str(c.__dict__.get("base_url"))


def test_http_error_becomes_a_typed_error():
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    c = JiraClient(base_url="https://x.atlassian.net", email="e", token="t", opener=opener)
    with pytest.raises(JiraError) as excinfo:
        c.myself()
    assert excinfo.value.status == 401


def test_module_contains_no_mutating_http_verb():
    source = (ROOT / "services" / "api" / "app" / "jira.py").read_text(encoding="utf-8")
    for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "method=\"POST"):
        assert verb not in source
    assert 'method="GET"' in source
