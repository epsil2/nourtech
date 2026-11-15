"""Control plane: projects, backlog proposals, Jira materialisation, Hermes executions,
deployments and metrics.

No network, no Hermes, no Jira: the adapters are replaced by fakes that behave like
the documented contracts. The point of these tests is to pin the guarantees the UI
depends on — idempotence, explicit approvals, honest failure states.
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
os.environ["CP_JOBS_SYNC"] = "1"

SPEC = {
    "name": "Veille IA hebdomadaire",
    "objective": "Produire une synthèse sourcée chaque lundi",
    "backlog": {
        "epic": {"title": "Veille IA hebdomadaire", "summary": "Synthèse sourcée des sorties IA"},
        "stories": [
            {
                "key": "S1",
                "title": "Collecter les sources",
                "as_a": "veilleur",
                "i_want": "collecter trois sources",
                "so_that": "la synthèse soit complète",
                "acceptance": [{"given": "trois flux", "when": "le run démarre", "then": "chaque item est horodaté"}],
                "tasks": ["Lister les flux", "Dédupliquer"],
                "depends_on": [],
                "definition_of_done": ["test écrit"],
            },
            {
                "key": "S2",
                "title": "Rédiger la synthèse",
                "as_a": "veilleur",
                "i_want": "rédiger 10 lignes",
                "so_that": "le lecteur décide vite",
                "acceptance": [],
                "tasks": ["Rédiger"],
                "depends_on": ["S1"],
            },
        ],
    },
}


@pytest.fixture()
def client():
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def _approved_spec(client) -> str:
    store = client.app.state.store
    agent = store.create_agent("veilleur", "veille IA")
    spec = store.create_agent_spec("veilleur", SPEC)
    store.approve_agent_spec(spec["id"], agent["id"], "soufiane")
    return spec["id"]


def _project(client, key: str = "VEILLE") -> dict:
    response = client.post(
        "/api/v1/projects",
        json={
            "key": key,
            "name": "Veille IA",
            "business_request": "Une synthèse hebdomadaire fiable",
            "objectives": ["synthèse lundi"],
            "kpis": ["délai < 1 j"],
            "jira_project": "SCRUM",
        },
        headers={"x-actor": "soufiane"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- proposals
def test_build_proposal_is_deterministic_and_hierarchical():
    from app.api_projects import build_proposal

    first = build_proposal({"payload": SPEC}, "VEILLE")
    second = build_proposal({"payload": SPEC}, "VEILLE")
    assert first == second
    assert [item["type"] for item in first] == ["Epic", "Story", "Subtask", "Subtask", "Story", "Subtask"]
    assert first[1]["parent"] == "EPIC"
    assert first[2]["parent"] == "S1"
    assert first[1]["acceptance"] and "alors" in first[1]["acceptance"][0]
    assert first[3]["key"] == "S1-2"
    assert first[5]["key"] == "S2-1"


def test_proposal_needs_an_approved_specification(client):
    project = _project(client)
    missing = client.post(f"/api/v1/projects/{project['id']}/proposals", json={})
    assert missing.status_code == 409

    spec_id = _approved_spec(client)
    created = client.post(f"/api/v1/projects/{project['id']}/proposals", json={"spec_id": spec_id})
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert proposal["status"] == "draft"
    assert proposal["summary"] == {"epics": 1, "stories": 2, "subtasks": 3}


def test_proposal_editing_is_limited_to_drafts_and_keeps_parents_valid(client):
    project = _project(client)
    spec_id = _approved_spec(client)
    proposal = client.post(f"/api/v1/projects/{project['id']}/proposals", json={"spec_id": spec_id}).json()

    broken = client.patch(
        f"/api/v1/proposals/{proposal['id']}",
        json={"items": [{"key": "S1", "type": "Story", "summary": "Orpheline", "parent": "S9"}]},
    )
    assert broken.status_code == 422

    edited = client.patch(
        f"/api/v1/proposals/{proposal['id']}",
        json={
            "items": [
                {"key": "EPIC", "type": "Epic", "summary": "Veille IA", "labels": ["control-plane"]},
                {"key": "S1", "type": "Story", "summary": "Collecter", "parent": "EPIC", "acceptance": ["horodater"]},
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    assert len(edited.json()["items"]) == 2

    client.post(
        f"/api/v1/proposals/{proposal['id']}/approve", json={"actor": "soufiane", "note": "ok"}
    )
    frozen = client.patch(
        f"/api/v1/proposals/{proposal['id']}",
        json={"items": [{"key": "EPIC", "type": "Epic", "summary": "changé"}]},
    )
    assert frozen.status_code == 409


def test_proposal_approval_requires_a_named_actor(client):
    project = _project(client)
    spec_id = _approved_spec(client)
    proposal = client.post(f"/api/v1/projects/{project['id']}/proposals", json={"spec_id": spec_id}).json()
    refused = client.post(f"/api/v1/proposals/{proposal['id']}/approve", json={"actor": "anonymous"})
    assert refused.status_code == 422
    accepted = client.post(f"/api/v1/proposals/{proposal['id']}/approve", json={"actor": "soufiane"})
    assert accepted.status_code == 200
    assert accepted.json()["proposal"]["status"] == "approved"
    assert accepted.json()["approval"]["actor"] == "soufiane"


# ---------------------------------------------------------------- materialise
class FakeWriter:
    enabled = True
    projects = ["SCRUM"]
    base_url = "https://nourtech.atlassian.net"

    def __init__(self):
        self.created: list[dict] = []
        FakeWriter.instance = self

    def create_issue(self, summary, issue_type="Story", description=None, project=None, labels=None, parent=None):
        self.created.append({"summary": summary, "type": issue_type, "parent": parent, "labels": labels})
        return {"key": f"SCRUM-{900 + len(self.created)}", "created": True, "url": "https://nourtech.atlassian.net/browse/x"}


def test_materialisation_requires_approval_then_is_idempotent(client, monkeypatch):
    from app import jira_write

    monkeypatch.setattr(jira_write, "JiraWriter", FakeWriter)
    project = _project(client)
    spec_id = _approved_spec(client)
    proposal = client.post(f"/api/v1/projects/{project['id']}/proposals", json={"spec_id": spec_id}).json()

    early = client.post(f"/api/v1/proposals/{proposal['id']}/materialize")
    assert early.status_code == 409

    client.post(f"/api/v1/proposals/{proposal['id']}/approve", json={"actor": "soufiane"})
    first = client.post(f"/api/v1/proposals/{proposal['id']}/materialize")
    assert first.status_code == 202, first.text
    job = client.get(first.json()["poll"]).json()
    assert job["status"] == "succeeded", job
    assert len(job["result"]["created"]) == 6
    assert job["result"]["failures"] == []

    writer = FakeWriter.instance
    assert writer.created[0]["type"] == "Epic"
    assert writer.created[2]["parent"] is not None  # subtask attached to a real key
    assert writer.created[2]["labels"][0] == "control-plane"

    second = client.post(f"/api/v1/proposals/{proposal['id']}/materialize")
    job2 = client.get(second.json()["poll"]).json()
    assert job2["result"]["created"] == []
    assert len(job2["result"]["existing"]) == 6
    detail = client.get(f"/api/v1/proposals/{proposal['id']}").json()
    assert len(detail["issues"]) == 6


def test_materialisation_is_refused_when_jira_writes_are_disabled(client, monkeypatch):
    from app import jira_write

    class Disabled(FakeWriter):
        enabled = False

    monkeypatch.setattr(jira_write, "JiraWriter", Disabled)
    project = _project(client)
    spec_id = _approved_spec(client)
    proposal = client.post(f"/api/v1/projects/{project['id']}/proposals", json={"spec_id": spec_id}).json()
    client.post(f"/api/v1/proposals/{proposal['id']}/approve", json={"actor": "soufiane"})
    refused = client.post(f"/api/v1/proposals/{proposal['id']}/materialize")
    assert refused.status_code == 409
    assert "disabled" in refused.json()["detail"]


# ---------------------------------------------------------------- executions
class FakeAdapter:
    available = True

    def __init__(self):
        self.blocked: list[tuple] = []
        self.dispatched: list[str] = []
        self.state = "todo"

    def status(self):
        return {"available": True, "binary": "/usr/bin/hermes", "engine": "hermes-kanban"}

    def ensure_board(self, slug, name=None):
        return slug

    def create_task(self, **kwargs):
        self.kwargs = kwargs
        return {"id": "kb_01", "status": "todo", "board": kwargs["board"]}

    def show(self, board, task_id):
        return {"id": task_id, "status": self.state, "finished_at": "2026-11-15T12:00:00+00:00"}

    def runs(self, board, task_id):
        return [{"run": 1, "status": "ok"}]

    def dispatch(self, board, max_spawns=1):
        self.dispatched.append(board)
        return {"spawned": 1}

    def block(self, board, task_id, reason):
        self.blocked.append((board, task_id, reason))
        return "blocked"


def test_execution_refuses_to_start_without_hermes(client, monkeypatch):
    from app import api_executions

    class Missing(FakeAdapter):
        available = False

    monkeypatch.setattr(api_executions, "_adapter", lambda: Missing())
    project = _project(client)
    refused = client.post(
        "/api/v1/executions", json={"project_id": project["id"], "objective": "collecter les flux"}
    )
    assert refused.status_code == 409
    assert client.get("/api/v1/executions").json()["total"] == 0


def test_execution_lifecycle_sync_and_cancel(client, monkeypatch):
    from app import api_executions

    adapter = FakeAdapter()
    monkeypatch.setattr(api_executions, "_adapter", lambda: adapter)
    project = _project(client)

    created = client.post(
        "/api/v1/executions",
        json={"project_id": project["id"], "objective": "collecter les flux", "dispatch": True},
        headers={"idempotency-key": "exec-1"},
    )
    assert created.status_code == 201, created.text
    execution = created.json()
    assert execution["task_id"] == "kb_01"
    assert execution["status"] == "queued"
    assert adapter.kwargs["idempotency_key"] == "exec-1"
    assert adapter.kwargs["board"] == "camilia-veille"
    assert "preuve" in adapter.kwargs["body"]

    again = client.post(
        "/api/v1/executions",
        json={"project_id": project["id"], "objective": "collecter les flux"},
        headers={"idempotency-key": "exec-1"},
    )
    assert again.json()["id"] == execution["id"]

    adapter.state = "done"
    synced = client.post(f"/api/v1/executions/{execution['id']}/sync").json()
    assert synced["status"] == "succeeded"
    assert synced["runs"] == 1

    detail = client.get(f"/api/v1/executions/{execution['id']}").json()
    kinds = [event["kind"] for event in detail["events"]]
    assert "created" in kinds and "status" in kinds

    late = client.post(f"/api/v1/executions/{execution['id']}/cancel", json={"reason": "trop tard"})
    assert late.status_code == 409


def test_cancel_blocks_the_kanban_task(client, monkeypatch):
    from app import api_executions

    adapter = FakeAdapter()
    monkeypatch.setattr(api_executions, "_adapter", lambda: adapter)
    project = _project(client)
    execution = client.post(
        "/api/v1/executions", json={"project_id": project["id"], "objective": "collecter les flux"}
    ).json()
    cancelled = client.post(f"/api/v1/executions/{execution['id']}/cancel", json={"reason": "budget"})
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert adapter.blocked[0][2] == "budget"

    adapter.state = "blocked"  # what Hermes reports after our block
    resynced = client.post(f"/api/v1/executions/{execution['id']}/sync").json()
    assert resynced["status"] == "cancelled"
    assert resynced["kanban_status"] == "blocked"


# ---------------------------------------------------------------- adapter contract
def test_adapter_flattens_show_and_normalises_epochs(monkeypatch):
    from app.hermes_kanban import KanbanAdapter

    adapter = KanbanAdapter()

    def fake_run(args, **kwargs):
        assert args[:4] == ["kanban", "--board", "camilia-veille", "show"]
        return {
            "task": {"id": "t_1", "status": "done", "completed_at": 1794752847},
            "runs": [{"id": 1}, {"id": 2}],
            "events": [{"id": 1}],
            "latest_summary": "ok",
        }

    monkeypatch.setattr(adapter, "_run", fake_run)
    task = adapter.show("camilia-veille", "t_1")
    assert task["status"] == "done"
    assert task["run_count"] == 2
    assert task["finished_at"] == "2026-11-15T14:27:27+00:00"
    assert task["latest_summary"] == "ok"


def test_adapter_refuses_a_task_id_that_could_become_a_flag():
    from app.hermes_kanban import KanbanAdapter

    adapter = KanbanAdapter()
    for bad in ("--help", "t_1 && rm -rf /", "", "t_1;drop"):
        with pytest.raises(ValueError):
            adapter.show("camilia-veille", bad)
        with pytest.raises(ValueError):
            adapter.block("camilia-veille", bad, "reason")


def test_boot_fails_executions_left_in_starting(client):
    store = client.app.state.store
    stale = store.cp_save("execution", {"project_id": "p", "objective": "interrompue", "status": "starting"})
    from app import api_executions

    api_executions.boot(store)
    assert store.cp_get("execution", stale["id"])["status"] == "failed"


# ---------------------------------------------------------------- deployments
def test_environments_are_seeded(client):
    names = [env["name"] for env in client.get("/api/v1/environments").json()["environments"]]
    assert names == ["DEV", "QA", "PRE-PROD", "PROD"]


def test_prod_deployment_needs_a_named_operator_and_evidence(client):
    project = _project(client)
    deployment = client.post(
        "/api/v1/deployments",
        json={"project_id": project["id"], "environment": "PROD", "version": "v1.2.3", "commit_sha": "abc1234"},
        headers={"x-actor": "soufiane"},
    )
    assert deployment.status_code == 201
    doc = deployment.json()
    assert doc["status"] == "awaiting_approval"

    anonymous = client.post(f"/api/v1/deployments/{doc['id']}/confirm", json={"outcome": "deployed"})
    assert anonymous.status_code == 422

    deployed = client.post(
        f"/api/v1/deployments/{doc['id']}/confirm",
        json={"outcome": "deployed", "actor": "soufiane", "evidence": "https://camilia.nourredine.tech healthz 200"},
    )
    assert deployed.status_code == 200
    assert deployed.json()["status"] == "deployed"

    rollback = client.post(f"/api/v1/deployments/{doc['id']}/rollback", json={"reason": "régression 500"})
    assert rollback.status_code == 201
    assert client.get(f"/api/v1/projects/{project['id']}").json()["deployments"][0]["status"] == "rolled_back"


def test_rollback_only_applies_to_a_deployed_release(client):
    project = _project(client)
    deployment = client.post(
        "/api/v1/deployments",
        json={"project_id": project["id"], "environment": "QA", "version": "v0.1"},
    ).json()
    refused = client.post(f"/api/v1/deployments/{deployment['id']}/rollback", json={"reason": "test"})
    assert refused.status_code == 409


def test_metrics_report_an_honest_cost_and_success_rate(client, monkeypatch):
    from app import api_executions

    adapter = FakeAdapter()
    monkeypatch.setattr(api_executions, "_adapter", lambda: adapter)
    project = _project(client)
    execution = client.post(
        "/api/v1/executions", json={"project_id": project["id"], "objective": "collecter les flux"}
    ).json()
    adapter.state = "done"
    client.post(f"/api/v1/executions/{execution['id']}/sync")

    metrics = client.get("/api/v1/cp/metrics").json()
    assert metrics["executions"]["total"] == 1
    assert metrics["success_rate"] == 1.0
    assert metrics["cost"] is None
    assert "non mesuré" in metrics["cost_note"]
