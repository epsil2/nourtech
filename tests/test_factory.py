"""Project Factory: state machine, idempotency, failure isolation and traceability.

Jira and GitHub are replaced by a recording fake at the HTTP boundary, so the tests
assert the real control flow (lookup → adopt → create → record) without touching
either service.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))


class FakeWorld:
    """Deterministic stand-in for Jira and GitHub."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.jira_projects: dict[str, dict] = {}
        self.repos: dict[str, dict] = {}
        self.issues: list[dict] = []
        self.repo_files: dict[str, dict] = {}
        self.fail_github_create = False
        self.fail_ci_commit = False

    # -- helpers ----------------------------------------------------------- #
    def count(self, method: str, needle: str) -> int:
        return sum(1 for m, url in self.calls if m == method and needle in url)

    def __call__(self, method, url, *, headers=None, body=None, timeout=30.0):
        self.calls.append((method, url))
        path = urlsplit(url).path
        if "atlassian.net" in url:
            return self._jira(method, path, body)
        if "api.github.com" in url:
            return self._github(method, path, body)
        return 404, {"message": "unexpected host"}

    def _jira(self, method, path, body):
        if path == "/rest/api/3/myself":
            return 200, {"accountId": "acc-1", "displayName": "Soufiane"}
        if path.startswith("/rest/api/3/project/") and method == "GET":
            key = path.rsplit("/", 1)[-1]
            if key in self.jira_projects:
                return 200, self.jira_projects[key]
            return 404, {"errorMessages": ["no project"]}
        if path == "/rest/api/3/project" and method == "POST":
            key = body["key"]
            if key in self.jira_projects:
                return 400, {"errorMessages": ["exists"]}
            self.jira_projects[key] = {"id": f"jira-{key}", "key": key, "name": body["name"]}
            return 201, self.jira_projects[key]
        if "/issue/createmeta/" in path:
            return 200, {
                "issueTypes": [
                    {"id": "10000", "name": "Epic"},
                    {"id": "10001", "name": "Story"},
                    {"id": "10002", "name": "Task"},
                ]
            }
        if path == "/rest/api/3/issue" and method == "POST":
            key = f"{body['fields']['project']['key']}-{len(self.issues) + 1}"
            issue = {"id": f"id-{len(self.issues) + 1}", "key": key, "fields": body["fields"]}
            self.issues.append(issue)
            return 201, {"id": issue["id"], "key": key}
        return 404, {"message": "unexpected jira call"}

    def _github(self, method, path, body):
        if method == "GET" and path.startswith("/repos/") and "/git/" not in path and "/actions/" not in path:
            full = "/".join(path.split("/")[2:4])
            if full in self.repos:
                return 200, self.repos[full]
            return 404, {"message": "Not Found"}
        if method == "POST" and path == "/user/repos":
            if self.fail_github_create:
                return 422, {"message": "repository creation is disabled"}
            full = f"epsil2/{body['name']}"
            self.repos[full] = {"full_name": full, "html_url": f"https://github.com/{full}", "default_branch": "main", "private": body["private"]}
            return 201, self.repos[full]
        if "/git/ref/heads/main" in path and method == "GET":
            return 200, {"object": {"sha": "parent-sha"}}
        if "/git/commits/parent-sha" in path and method == "GET":
            return 200, {"tree": {"sha": "base-tree"}}
        if path.endswith("/git/trees") and method == "POST":
            return 201, {"sha": f"tree-{len(self.repo_files) + 1}"}
        if path.endswith("/git/commits") and method == "POST":
            if self.fail_ci_commit and "ci:" in json.dumps(body):
                return 422, {"message": "commit rejected"}
            self.repo_files.setdefault("files", {}).update({item["path"]: item["content"] for item in []} or {})
            return 201, {"sha": "commit-sha"}
        if "/git/refs/heads/main" in path and method == "PATCH":
            return 200, {"object": {"sha": body["sha"]}}
        if "/actions/runs" in path:
            return 200, {"workflow_runs": [{"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "html_url": "https://github.com/x/y/actions/runs/1", "head_sha": "commit-sha"}]}
        return 404, {"message": f"unexpected github call {method} {path}"}


class FakeKanban:
    """Stand-in for the Hermes CLI adapter: records boards and tasks, never spawns."""

    created: list[dict] = []

    def __init__(self, *a, **k) -> None:
        pass

    @property
    def available(self) -> bool:
        return True

    def ensure_board(self, slug: str, name: str | None = None) -> str:
        FakeKanban.created.append({"board": slug, "name": name})
        return slug

    def create_task(self, **kwargs):
        FakeKanban.created.append(kwargs)
        return {"id": f"t_{len(FakeKanban.created)}", "status": "queued"}

    def show(self, board, task_id):
        return {"id": task_id, "status": "running", "runs": []}


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("JIRA_SITE_URL", "https://nourtech.atlassian.net")
    monkeypatch.setenv("JIRA_ACCOUNT_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "jira-test-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-test-token")
    monkeypatch.setenv("CAMILIA_GITHUB_OWNER", "epsil2")
    monkeypatch.setenv("CP_JOBS_SYNC", "1")
    fake = FakeWorld()
    import app.hermes_kanban as kanban
    import app.project_factory as factory

    FakeKanban.created = []
    monkeypatch.setattr(factory, "_http", fake)
    monkeypatch.setattr(kanban, "KanbanAdapter", FakeKanban)
    return fake


@pytest.fixture()
def client(world):
    main = importlib.import_module("app.main")
    importlib.reload(main)
    importlib.reload(importlib.import_module("app.api_factory"))
    with TestClient(main.app) as c:
        yield c


def make_project(client, key="FACT1", name="Factory Test"):
    response = client.post(
        "/api/v1/factory/projects",
        json={
            "key": key,
            "name": name,
            "description": "Un projet de test non-production",
            "business_objective": "Prouver la chaîne de provisioning",
            "project_type": "Backend API",
            "business_domain": "Tests",
            "owner": "soufiane",
            "visibility": "private",
            "template": "fastapi-api",
            "requirements": "Exposer une API de suivi\nPermettre la création d'un élément\nJournaliser les actions",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]


def spec_and_approve(client, project_id):
    assert client.post(f"/api/v1/factory/projects/{project_id}/spec", json={}).status_code == 200
    assert client.post(f"/api/v1/factory/projects/{project_id}/spec/submit").status_code == 200
    approved = client.post(f"/api/v1/factory/projects/{project_id}/approve", json={"notes": "ok"})
    assert approved.status_code == 200, approved.text
    return approved.json()


# --------------------------------------------------------------------------- #
# creation and lifecycle
# --------------------------------------------------------------------------- #
def test_project_is_created_as_draft_with_a_specification(client):
    project = make_project(client)
    assert project["state"] == "DRAFT"
    assert project["project"]["key"] == "FACT1"

    written = client.post(f"/api/v1/factory/projects/{project['project']['id']}/spec", json={})
    body = written.json()
    assert body["spec"]["version"] == 1
    assert body["spec"]["content"]["generator"] == "deterministic-draft"
    assert body["spec"]["content"]["functional_requirements"]
    assert body["project"]["state"] == "SPECIFICATION_READY"


def test_duplicate_key_is_refused(client):
    make_project(client, key="DUPL")
    again = client.post(
        "/api/v1/factory/projects",
        json={"key": "DUPL", "name": "Other", "template": "fastapi-api"},
    )
    assert again.status_code == 409


def test_provisioning_requires_a_human_approval(client):
    project = make_project(client, key="NOAPP")
    client.post(f"/api/v1/factory/projects/{project['project']['id']}/spec", json={})
    response = client.post(f"/api/v1/factory/projects/{project['project']['id']}/provision")
    assert response.status_code == 409
    assert "approval" in response.json()["detail"].lower()


def test_provisioning_fails_closed_before_approval_state(client):
    project = make_project(client, key="DRAFT1")
    response = client.post(f"/api/v1/factory/projects/{project['project']['id']}/provision")
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# the whole chain
# --------------------------------------------------------------------------- #
def test_full_provisioning_reaches_ready(client, world):
    project = make_project(client)
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)

    started = client.post(f"/api/v1/factory/projects/{project_id}/provision")
    assert started.status_code == 202, started.text

    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert detail["state"] == "READY", detail["operations"]
    assert detail["jira"]["jira_key"] == "FACT1"
    assert detail["repository"]["full_name"].startswith("epsil2/fact1")
    assert detail["workspace"]["board"]
    assert {op["step"] for op in detail["operations"]} == {
        "create_jira", "create_repository", "apply_template",
        "configure_cicd", "register_workspace", "validate",
    }
    assert all(op["status"] == "succeeded" for op in detail["operations"])
    assert all(op["idempotency_key"] for op in detail["operations"])
    assert detail["ci_runs"] and detail["ci_runs"][0]["conclusion"] == "success"
    assert detail["next_step"] is None


def test_provisioning_twice_does_not_duplicate_resources(client, world):
    project = make_project(client, key="IDEM")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    first = len(world.calls)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    # nothing new is created: the state machine has no pending step
    assert world.count("POST", "/rest/api/3/project") == 1
    assert world.count("POST", "/user/repos") == 1
    assert len(world.calls) == first


def test_failure_keeps_jira_and_records_the_github_error(client, world):
    world.fail_github_create = True
    project = make_project(client, key="FAIL1")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")

    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert detail["state"] == "FAILED"
    assert detail["jira"]["jira_key"] == "FAIL1", "the Jira project must be kept"
    failed = [op for op in detail["operations"] if op["status"] == "failed"]
    assert failed and failed[0]["step"] == "create_repository"
    assert "422" in failed[0]["error"]
    assert detail["provisioning"]["failed_step"] == "create_repository"


def test_retry_resumes_at_the_failed_step_without_recreating_jira(client, world):
    world.fail_github_create = True
    project = make_project(client, key="RESUME")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    assert world.count("POST", "/rest/api/3/project") == 1

    world.fail_github_create = False
    retried = client.post(f"/api/v1/factory/projects/{project_id}/retry")
    assert retried.status_code == 200, retried.text

    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert detail["state"] == "READY", detail["operations"]
    assert world.count("POST", "/rest/api/3/project") == 1, "Jira must not be created twice"
    create = next(op for op in detail["operations"] if op["step"] == "create_jira")
    assert create["attempt"] == 1


def test_existing_jira_project_is_adopted(client, world):
    world.jira_projects["ADOPT"] = {"id": "jira-ADOPT", "key": "ADOPT", "name": "Already there"}
    project = make_project(client, key="ADOPT")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    assert world.count("POST", "/rest/api/3/project") == 0
    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert detail["jira"]["created"] is False
    assert detail["state"] == "READY"


# --------------------------------------------------------------------------- #
# backlog
# --------------------------------------------------------------------------- #
def test_backlog_is_created_once_and_not_duplicated(client, world):
    project = make_project(client, key="BACK1")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")

    first = client.post(f"/api/v1/factory/projects/{project_id}/backlog")
    assert first.status_code == 202, first.text
    count_after_first = len(world.issues)
    assert count_after_first >= 5

    client.post(f"/api/v1/factory/projects/{project_id}/backlog")
    assert len(world.issues) == count_after_first, "second run must not create issues again"

    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert len(detail["jira_issues"]) == count_after_first
    assert {issue["issue_type"] for issue in detail["jira_issues"]} >= {"Epic", "Story"}
    assert all(issue["key"] for issue in detail["jira_issues"])


# --------------------------------------------------------------------------- #
# traceability and guards
# --------------------------------------------------------------------------- #
def test_audit_trail_records_each_step(client, world):
    project = make_project(client, key="AUDIT1")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    rows = client.get(f"/api/v1/factory/projects/{project_id}/audit").json()["items"]
    actions = {row["action"] for row in rows}
    assert "factory.project_created" in actions
    assert "provisioning.create_jira" in actions
    assert "provisioning.ready" in actions


def test_project_is_locked_once_provisioning_started(client, world):
    project = make_project(client, key="LOCK1")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    response = client.patch(f"/api/v1/factory/projects/{project_id}", json={"name": "Renamed"})
    assert response.status_code == 409


def test_unknown_template_is_refused(client):
    response = client.post(
        "/api/v1/factory/projects",
        json={"key": "BADTPL", "name": "Nope", "template": "kubernetes-mesh"},
    )
    assert response.status_code == 422


def test_catalogue_exposes_readiness_and_templates(client):
    body = client.get("/api/v1/factory/catalogue").json()
    assert {t["key"] for t in body["templates"]} == {
        "react-fastapi", "fastapi-api", "python-agent", "react-fastapi-rag"
    }
    assert body["jira"]["ready"] is True
    assert body["github"]["ready"] is True
    assert body["steps"]


def test_development_task_cannot_start_before_ready(client, world):
    project = make_project(client, key="DEV0")
    project_id = project["project"]["id"]
    response = client.post(
        f"/api/v1/factory/projects/{project_id}/development", json={"issue_key": "DEV0-1"}
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# bounded Hermes development task
# --------------------------------------------------------------------------- #
def test_bounded_development_task_is_launched_with_limits(client, world):
    project = make_project(client, key="DEVL")
    project_id = project["project"]["id"]
    spec_and_approve(client, project_id)
    client.post(f"/api/v1/factory/projects/{project_id}/provision")
    assert client.get(f"/api/v1/factory/projects/{project_id}").json()["state"] == "READY"

    response = client.post(
        f"/api/v1/factory/projects/{project_id}/development",
        json={"issue_key": "DEVL-1", "runtime_limit": "30m", "retry_limit": 1},
    )
    assert response.status_code == 202, response.text
    execution = response.json()["execution"]
    assert execution["status"] == "queued"
    assert execution["idempotency_key"].startswith("camila:")

    task = next(item for item in FakeKanban.created if item.get("idempotency_key"))
    assert task["max_runtime"] == "30m"
    assert task["max_retries"] == 1
    assert task["workspace"] == "worktree"
    assert "DEVL-1" in task["body"] and "do not deploy" in task["body"]

    detail = client.get(f"/api/v1/factory/projects/{project_id}").json()
    assert detail["state"] == "DEVELOPMENT_RUNNING"
    assert detail["executions"] and detail["executions"][0]["task_id"]
