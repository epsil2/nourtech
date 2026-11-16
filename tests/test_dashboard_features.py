"""Dashboard features: GitHub visibility, Jira board, monitoring, integrations, agent edits.

Every external call is stubbed: these tests describe the contract the UI relies on,
not the network. Nothing here reaches Jira or GitHub.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ["FACTORY_STORE"] = "memory"


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


class FakeGitHub:
    repo = "epsil2/nourtech"
    configured = True
    token_present = True

    def repo_info(self):
        return {"full_name": self.repo, "default_branch": "main", "private": False}

    def pulls(self, state="all", limit=10):
        return [{"number": 14, "title": "wizard", "state": "open", "merged": False}]

    def commits(self, limit=10):
        return [{"sha": "f9b09a1", "message": "feat(dashboard): sprint 2", "author": "S"}]

    def checks(self, sha):
        return [{"name": "verify", "status": "completed", "conclusion": "success"}]

    def workflow_runs(self, limit=10):
        return [{"name": "ci", "status": "completed", "conclusion": "success"}]

    def releases(self, limit=10):
        return [{"tag": "v0.1.0", "published_at": "2026-11-14T18:00:00Z"}]


class FakeJira:
    configured = True
    site = "https://nourtech.atlassian.net"

    def board_issues(self, jql, limit=100):
        return [
            {
                "key": "SCRUM-1",
                "summary": "Premier ticket",
                "type": "Story",
                "status": "In Progress",
                "assignee": "Epsilon Nour",
                "priority": "Medium",
                "labels": [],
                "parent": None,
                "sprints": [{"id": 5, "name": "Sprint 1", "state": "closed"}],
                "created": "2026-11-02",
                "updated": "2026-11-03",
                "url": "https://x/browse/SCRUM-1",
            },
            {
                "key": "SCRUM-2",
                "summary": "Deuxième ticket",
                "type": "Task",
                "status": "Done",
                "assignee": None,
                "priority": None,
                "labels": ["ai-factory"],
                "parent": "SCRUM-1",
                "sprints": [],
                "created": "2026-11-02",
                "updated": "2026-11-04",
                "url": None,
            },
        ]

    def sprints(self, board=1):
        return [
            {"id": 5, "name": "Sprint 1", "state": "closed", "start": "2026-11-02", "end": "2026-11-06"},
            {"id": 9, "name": "Sprint 5", "state": "future", "start": "2026-11-30", "end": "2026-12-04"},
        ]


# -- github ---------------------------------------------------------------
def test_github_routes_are_read_only_and_shaped(client, monkeypatch):
    monkeypatch.setattr("app.api_github.GitHubClient", FakeGitHub)
    status = client.get("/api/v1/integrations/github/status").json()
    assert status["configured"] is True
    assert status["mode"] == "read-only"

    pulls = client.get("/api/v1/integrations/github/pulls").json()
    assert pulls["count"] == 1 and pulls["items"][0]["number"] == 14

    overview = client.get("/api/v1/integrations/github/overview").json()
    assert set(overview) == {"repo", "pulls", "commits", "actions", "releases"}


def test_github_rejects_a_non_hex_sha(client, monkeypatch):
    monkeypatch.setattr("app.api_github.GitHubClient", FakeGitHub)
    response = client.get("/api/v1/integrations/github/checks/not-a-sha")
    assert response.status_code == 422


def test_github_unconfigured_is_503(client, monkeypatch):
    class Off(FakeGitHub):
        configured = False
        repo = ""

        def repo_info(self):
            from app.github import GitHubNotConfigured

            raise GitHubNotConfigured("GITHUB_TOKEN and GITHUB_REPO must be set")

    monkeypatch.setattr("app.api_github.GitHubClient", Off)
    assert client.get("/api/v1/integrations/github/status").json()["configured"] is False
    assert client.get("/api/v1/integrations/github/repo").status_code == 503


# -- jira board -----------------------------------------------------------
def test_backlog_counts_by_status_and_type(client, monkeypatch):
    monkeypatch.setattr("app.api_jira_board.JiraClient", FakeJira)
    body = client.get("/api/v1/integrations/jira/board/backlog").json()
    assert body["count"] == 2
    assert body["by_status"] == {"Done": 1, "In Progress": 1}
    assert body["by_type"] == {"Story": 1, "Task": 1}


def test_sprints_carry_issue_counts_and_active_is_honest(client, monkeypatch):
    monkeypatch.setattr("app.api_jira_board.JiraClient", FakeJira)
    body = client.get("/api/v1/integrations/jira/board/sprints").json()
    assert body["count"] == 2
    assert body["active"] is None  # nothing is active: the UI must say so
    sprint_one = next(s for s in body["items"] if s["name"] == "Sprint 1")
    assert sprint_one["issues"] == 1


def test_backlog_project_must_look_like_a_jira_key(client, monkeypatch):
    monkeypatch.setattr("app.api_jira_board.JiraClient", FakeJira)
    assert client.get("/api/v1/integrations/jira/board/backlog?project=scrum").status_code == 422


# -- backlog filtering (project / sprint / feature / user story) ----------
def _issue(key, summary, type_, status="To Do", parent=None, sprints=None):
    return {
        "key": key,
        "summary": summary,
        "type": type_,
        "status": status,
        "assignee": None,
        "priority": None,
        "labels": [],
        "parent": parent,
        "sprints": sprints or [],
        "created": "2026-11-02",
        "updated": "2026-11-04",
        "url": f"https://x/browse/{key}",
    }


class FilterJira(FakeJira):
    """One epic with two stories, one story in a sprint, one loose task."""

    def board_issues(self, jql, limit=100):
        assert "project = SCRUM" in jql or "project = RM002" in jql
        return [
            _issue("SCRUM-100", "Gouvernance des agents", "Epic"),
            _issue("SCRUM-101", "Publier un backlog", "Story", parent="SCRUM-100",
                   sprints=[{"id": 10, "name": "Sprint 6", "state": "future"}]),
            _issue("SCRUM-102", "Relire un ticket", "Story", parent="SCRUM-100"),
            _issue("SCRUM-103", "Tâche d'infrastructure", "Task"),
        ]

    def projects(self):
        return [
            {"key": "SCRUM", "name": "Ai Factory Software Team", "style": "next-gen"},
            {"key": "RM002", "name": "IT Release Readiness", "style": "next-gen"},
        ]

    def boards(self):
        return [{"id": 1, "name": "SCRUM board", "type": "simple", "project_key": "SCRUM"}]


def _fresh():
    from app import api_jira_board

    api_jira_board._cache.clear()


def test_projects_endpoint_pairs_each_project_with_its_board(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    body = client.get("/api/v1/integrations/jira/board/projects").json()
    assert body["count"] == 2
    row = next(p for p in body["items"] if p["key"] == "SCRUM")
    assert row["board"] == 1
    assert next(p for p in body["items"] if p["key"] == "RM002")["board"] is None


def test_backlog_facets_drive_the_four_drop_downs(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    body = client.get("/api/v1/integrations/jira/board/backlog?project=SCRUM").json()
    assert body["total"] == 4 and body["count"] == 4
    assert [f["key"] for f in body["facets"]["features"]] == ["SCRUM-100"]
    assert body["facets"]["features"][0]["issues"] == 2
    assert [s["key"] for s in body["facets"]["stories"]] == ["SCRUM-101", "SCRUM-102", "SCRUM-103"]
    assert [s["name"] for s in body["facets"]["sprints"]] == ["Sprint 6"]
    assert body["facets"]["backlog_only"] == 3
    assert body["filters"]["active"] == 0


def test_feature_selection_lists_the_feature_and_its_stories(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    body = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&feature=SCRUM-100"
    ).json()
    assert [i["key"] for i in body["items"]] == ["SCRUM-100", "SCRUM-101", "SCRUM-102"]
    # facets stay complete: the other selections must remain reachable
    assert len(body["facets"]["features"]) == 1
    assert len(body["facets"]["stories"]) == 3


def test_story_selection_narrows_to_one_issue(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    body = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&story=SCRUM-102"
    ).json()
    assert [i["key"] for i in body["items"]] == ["SCRUM-102"]
    assert body["filters"]["story"] == "SCRUM-102"
    assert body["filters"]["active"] == 1


def test_sprint_selection_and_the_no_sprint_bucket(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    in_sprint = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&sprint=10"
    ).json()
    assert [i["key"] for i in in_sprint["items"]] == ["SCRUM-101"]
    no_sprint = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&sprint=none"
    ).json()
    assert [i["key"] for i in no_sprint["items"]] == ["SCRUM-100", "SCRUM-102", "SCRUM-103"]


def test_filters_combine_and_counts_describe_what_is_shown(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    body = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&feature=SCRUM-100&sprint=10"
    ).json()
    assert [i["key"] for i in body["items"]] == ["SCRUM-101"]
    assert body["count"] == 1 and body["total"] == 4
    assert body["by_type"] == {"Story": 1}
    assert body["by_status"] == {"To Do": 1}

    searched = client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&q=relire"
    ).json()
    assert [i["key"] for i in searched["items"]] == ["SCRUM-102"]


def test_backlog_rejects_a_malformed_feature_key(client, monkeypatch):
    _fresh()
    monkeypatch.setattr("app.api_jira_board.JiraClient", FilterJira)
    assert client.get(
        "/api/v1/integrations/jira/board/backlog?project=SCRUM&feature=nope"
    ).status_code == 422


# -- monitoring -----------------------------------------------------------
def test_system_snapshot_reports_real_numbers(client):
    body = client.get("/api/v1/system").json()
    host = body["host"]
    assert host["cpu_count"] and host["cpu_count"] >= 1
    assert 0 <= host["disk"]["used_percent"] <= 100
    assert host["memory"]["total_kb"] > 0
    assert body["factory"]["agents"] == 0
    assert body["gates"] == {
        "human_approval_required": True,
        "jira_write_enabled": body["gates"]["jira_write_enabled"],
        "deployment_requires_go_no_go": True,
    }


def test_system_events_start_empty(client):
    body = client.get("/api/v1/system/events").json()
    assert body["count"] == 0 and body["items"] == []


# -- integrations ---------------------------------------------------------
def test_integrations_never_return_a_secret(client, monkeypatch):
    monkeypatch.setattr("app.api_integrations.JiraClient", FakeJira)
    monkeypatch.setattr("app.api_integrations.GitHubClient", FakeGitHub)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-appear")
    body = client.get("/api/v1/integrations").json()

    anthropic = next(i for i in body["items"] if i["id"] == "anthropic")
    assert anthropic["configured"] is True
    assert anthropic["missing"] == []
    assert "sk-ant-should-never-appear" not in str(body)

    openai = next(i for i in body["items"] if i["id"] == "openai")
    assert openai["configured"] is False
    assert openai["missing"] == ["OPENAI_API_KEY"]
    assert body["suggestions"], "the page must be able to suggest tools"


# -- agent management -----------------------------------------------------
def test_agent_can_be_renamed_and_the_change_is_audited(client):
    created = client.post("/api/v1/agents", json={"name": "radar", "description": "veille"}).json()
    patched = client.patch(
        f"/api/v1/agents/{created['id']}",
        json={"description": "veille IA hebdomadaire"},
        headers={"x-actor": "soufiane"},
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "veille IA hebdomadaire"
    events = client.get("/api/v1/system/events").json()["items"]
    assert any(e["action"] == "agent.updated" and e["actor"] == "soufiane" for e in events)


def test_renaming_onto_an_existing_name_is_409(client):
    client.post("/api/v1/agents", json={"name": "alpha", "description": ""})
    beta = client.post("/api/v1/agents", json={"name": "beta", "description": ""}).json()
    response = client.patch(f"/api/v1/agents/{beta['id']}", json={"name": "alpha"})
    assert response.status_code == 409


def test_patch_without_fields_is_422_and_unknown_agent_is_404(client):
    created = client.post("/api/v1/agents", json={"name": "gamma", "description": ""}).json()
    assert client.patch(f"/api/v1/agents/{created['id']}", json={}).status_code == 422
    assert client.patch("/api/v1/agents/00000000-0000-0000-0000-000000000000", json={"description": "x"}).status_code == 404
