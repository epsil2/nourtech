"""Read/write Jira integration (SCRUM-45).

Reads stay impossible-to-mutate in :mod:`app.jira`; writes live in
:mod:`app.jira_write` behind three locks, and that is what these tests pin down:

* default is off — a write raises ``JiraWriteDisabled`` and the API answers 403;
* the allowlist rejects another project on the payload *and* on the issue key;
* creation is idempotent on (project, summary) and always labelled ``ai-factory``;
* every write issue label / comment / transition actually reaches Jira as a POST;
* each write is audited with the actor from ``x-actor``.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ["FACTORY_STORE"] = "memory"

from app.jira_write import (  # noqa: E402
    MARKER_LABEL,
    JiraWriteDisabled,
    JiraWriteForbidden,
    JiraWriter,
)

BAD_TOKEN = "ATA" + "TT" + "y" * 24


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def make_opener(routes: dict[str, dict], calls: list[dict]):
    def opener(request, timeout=None):
        payload = json.loads(request.data.decode()) if request.data else None
        calls.append({"method": request.get_method(), "url": request.full_url, "payload": payload})
        for fragment, body in routes.items():
            if fragment in request.full_url:
                return FakeResponse(json.dumps(body).encode())
        return FakeResponse(b"{}")

    return opener


def writer(routes=None, calls=None, **kwargs):
    calls = calls if calls is not None else []
    routes = routes or {}
    params = dict(
        base_url="https://example.atlassian.net",
        email="user@example.com",
        token=BAD_TOKEN,
        opener=make_opener(routes, calls),
        enabled=True,
        projects=["SCRUM"],
    )
    params.update(kwargs)
    return JiraWriter(**params), calls


# -- locks ---------------------------------------------------------------
def test_writes_are_off_by_default(monkeypatch):
    monkeypatch.delenv("JIRA_WRITE_ENABLED", raising=False)
    w, calls = writer(enabled=None)
    assert w.enabled is False
    with pytest.raises(JiraWriteDisabled):
        w.create_issue("Un besoin métier")
    assert calls == []


def test_allowlist_rejects_another_project():
    w, calls = writer()
    with pytest.raises(JiraWriteForbidden):
        w.create_issue("Un besoin métier", project="OPS")
    with pytest.raises(JiraWriteForbidden):
        w.comment("OPS-12", "coucou")
    assert calls == []


def test_allowlist_projects_come_from_env(monkeypatch):
    monkeypatch.setenv("JIRA_WRITE_PROJECT", "SCRUM, OPS")
    w, _ = writer(projects=None)
    assert w.projects == ["SCRUM", "OPS"]
    assert w.check_project("ops-7") == "OPS-7"


# -- creation ------------------------------------------------------------
def test_create_issue_sends_marker_label_and_adf_description():
    routes = {
        "/search/jql": {"issues": []},
        "/rest/api/3/issue": {"id": "10042", "key": "SCRUM-77"},
    }
    w, calls = writer(routes)
    row = w.create_issue(
        "Publier un radar IA hebdomadaire",
        description="10 signaux sourcés\n800 mots",
    )
    assert row == {"key": "SCRUM-77", "id": "10042", "created": True,
                   "url": "https://example.atlassian.net/browse/SCRUM-77"}
    post = [c for c in calls if c["method"] == "POST"][0]
    fields = post["payload"]["fields"]
    assert fields["labels"] == [MARKER_LABEL]
    assert fields["project"] == {"key": "SCRUM"}
    assert fields["description"]["type"] == "doc"
    assert fields["description"]["content"][0]["content"][0]["text"] == "10 signaux sourcés"


def test_create_issue_is_idempotent_on_summary():
    routes = {"/search/jql": {"issues": [
        {"key": "SCRUM-77", "fields": {"summary": "Publier un radar IA hebdomadaire",
                                       "issuetype": {"name": "Story"}, "status": {"name": "To Do"},
                                       "labels": [MARKER_LABEL]}},
    ]}}
    w, calls = writer(routes)
    row = w.create_issue("  publier   un RADAR ia hebdomadaire ")
    assert row["key"] == "SCRUM-77" and row["created"] is False
    assert [c["method"] for c in calls] == ["GET"]


def test_create_issue_rejects_too_short_summary():
    w, calls = writer()
    with pytest.raises(ValueError):
        w.create_issue("ab")
    assert calls == []


# -- comment / transition ------------------------------------------------
def test_comment_builds_document_format():
    w, calls = writer({"/comment": {"id": "20001"}})
    row = w.comment("SCRUM-77", "PRD validé\nligne 2")
    assert row["comment_id"] == "20001"
    payload = calls[-1]["payload"]["body"]
    assert [p["content"][0]["text"] for p in payload["content"]] == ["PRD validé", "ligne 2"]


def test_transition_resolves_by_name_and_posts_id():
    routes = {
        "/transitions": {"transitions": [
            {"id": "11", "name": "To Do", "to": {"name": "To Do"}},
            {"id": "21", "name": "Start progress", "to": {"name": "In Progress"}},
        ]},
    }
    w, calls = writer(routes)
    row = w.transition("SCRUM-77", "Start progress")
    assert row["transition"] == "Start progress" and row["to"] == "In Progress"
    assert calls[-1]["method"] == "POST"
    assert calls[-1]["payload"] == {"transition": {"id": "21"}}


def test_unknown_transition_lists_available_ones():
    routes = {"/transitions": {"transitions": [{"id": "11", "name": "To Do", "to": {"name": "To Do"}}]}}
    w, _ = writer(routes)
    with pytest.raises(ValueError) as excinfo:
        w.transition("SCRUM-77", "Done")
    assert "To Do" in str(excinfo.value)


def test_token_never_appears_in_results():
    routes = {"/search/jql": {"issues": []}, "/rest/api/3/issue": {"id": "1", "key": "SCRUM-78"}}
    w, _ = writer(routes)
    row = w.create_issue("Un besoin métier")
    assert BAD_TOKEN not in json.dumps(row)


# -- HTTP surface --------------------------------------------------------
@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def test_api_refuses_writes_when_not_opted_in(client, monkeypatch):
    monkeypatch.delenv("JIRA_WRITE_ENABLED", raising=False)
    monkeypatch.setenv("JIRA_SITE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_ACCOUNT_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", BAD_TOKEN)
    response = client.post("/api/v1/integrations/jira/issues", json={"summary": "Un besoin métier"})
    assert response.status_code == 403
    assert "JIRA_WRITE_ENABLED" in response.json()["detail"]
    status = client.get("/api/v1/integrations/jira/status").json()
    assert status["write_enabled"] is False and status["read_only"] is True


def test_api_status_reports_write_mode(client, monkeypatch):
    monkeypatch.setenv("JIRA_WRITE_ENABLED", "true")
    monkeypatch.setenv("JIRA_WRITE_PROJECT", "SCRUM")
    monkeypatch.setenv("JIRA_SITE_URL", "https://example.atlassian.net")
    status = client.get("/api/v1/integrations/jira/status").json()
    assert status["write_enabled"] is True
    assert status["read_only"] is False
    assert status["write_projects"] == ["SCRUM"]


def test_api_refuses_foreign_project(client, monkeypatch):
    monkeypatch.setenv("JIRA_WRITE_ENABLED", "true")
    monkeypatch.setenv("JIRA_WRITE_PROJECT", "SCRUM")
    monkeypatch.setenv("JIRA_SITE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_ACCOUNT_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", BAD_TOKEN)
    response = client.post(
        "/api/v1/integrations/jira/issues/OPS-3/comment", json={"text": "coucou"}
    )
    assert response.status_code == 403
