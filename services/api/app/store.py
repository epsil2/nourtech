"""Persistence for the Factory API (SCRUM-22, SCRUM-32).

Two implementations behind one interface:

* ``PgStore`` — PostgreSQL, used by DEV, Qualité and Production.
* ``MemoryStore`` — process-local, selected with ``FACTORY_STORE=memory`` so the
  API contract can be tested without a database.

The schema is created by ``PgStore.init()`` at startup. It is additive only:
no DROP, no destructive ALTER, so a redeploy of a newer image against an older
database is safe.
"""
from __future__ import annotations

import os
from decimal import Decimal
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id          uuid PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    description text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS runs (
    id         uuid PRIMARY KEY,
    agent_id   uuid NULL REFERENCES agents(id) ON DELETE SET NULL,
    objective  text NOT NULL,
    status     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL,
    action      text NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS prds (
    id         uuid PRIMARY KEY,
    source_key text NULL,
    payload    jsonb NOT NULL,
    status     text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS plans (
    id                uuid PRIMARY KEY,
    issue_key         text NOT NULL,
    status            text NOT NULL DEFAULT 'draft',
    payload           jsonb NOT NULL,
    workflow_id       text NULL,
    pull_request_url  text NULL,
    approved_by       text NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS evaluations (
    id                bigserial PRIMARY KEY,
    subject           text NOT NULL,
    dataset           text NOT NULL,
    score             numeric(5,4) NOT NULL,
    threshold         numeric(5,4) NOT NULL,
    passed            boolean NOT NULL,
    critical_failures jsonb NOT NULL DEFAULT '[]'::jsonb,
    results           jsonb NOT NULL DEFAULT '[]'::jsonb,
    review_outcome    text NULL,
    review_actor      text NULL,
    review_note       text NOT NULL DEFAULT '',
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS plans_issue_idx ON plans (issue_key);
CREATE INDEX IF NOT EXISTS evaluations_subject_idx ON evaluations (subject);
CREATE TABLE IF NOT EXISTS workflows (
    id           uuid PRIMARY KEY,
    definition   text NOT NULL,
    status       text NOT NULL,
    current_step text NULL,
    state        jsonb NOT NULL DEFAULT '{}'::jsonb,
    node_count   int NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id          bigserial PRIMARY KEY,
    workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    seq         int NOT NULL,
    step        text NOT NULL,
    kind        text NOT NULL,
    state       jsonb NOT NULL DEFAULT '{}'::jsonb,
    decision    text NULL,
    actor       text NULL,
    note        text NOT NULL DEFAULT '',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artifacts (
    commit_sha   text PRIMARY KEY,
    version      text NOT NULL,
    workflow     text NOT NULL,
    source       text NOT NULL DEFAULT 'github',
    image        text NOT NULL,
    built_at     text NOT NULL,
    tests        jsonb NOT NULL DEFAULT '{}'::jsonb,
    stories      jsonb NOT NULL DEFAULT '[]'::jsonb,
    signature    text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS releases (
    id               uuid PRIMARY KEY,
    version          text NOT NULL,
    commit_sha       text NOT NULL,
    target           text NOT NULL,
    status           text NOT NULL DEFAULT 'draft',
    report           jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes            text NOT NULL DEFAULT '',
    approved_by      text NULL,
    published_at     timestamptz NULL,
    rolled_back_to   uuid NULL,
    rollback_reason  text NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS releases_commit_idx ON releases (commit_sha);
CREATE INDEX IF NOT EXISTS releases_status_idx ON releases (status);
CREATE INDEX IF NOT EXISTS workflows_status_idx ON workflows (status);
CREATE INDEX IF NOT EXISTS checkpoints_workflow_idx ON checkpoints (workflow_id, seq);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_events (entity_type, entity_id);
CREATE TABLE IF NOT EXISTS agent_specs (
    id           uuid PRIMARY KEY,
    slug         text NOT NULL,
    status       text NOT NULL DEFAULT 'draft',
    payload      jsonb NOT NULL,
    agent_id     uuid NULL REFERENCES agents(id) ON DELETE SET NULL,
    approved_by  text NULL,
    approved_at  timestamptz NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_specs_status_idx ON agent_specs (status);
CREATE INDEX IF NOT EXISTS agent_specs_slug_idx ON agent_specs (slug);
"""


def new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    """Non-persistent store: tests and local smoke runs only."""

    def __init__(self) -> None:
        self.agents: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.prds: dict[str, dict[str, Any]] = {}
        self.workflows: dict[str, dict[str, Any]] = {}
        self.checkpoints: list[dict[str, Any]] = []
        self.plans: dict[str, dict[str, Any]] = {}
        self.agent_specs: dict[str, dict[str, Any]] = {}
        self.evaluations: list[dict[str, Any]] = []
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.releases: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []

    def init(self) -> None:
        return None

    def close(self) -> None:
        return None

    # agents
    def create_agent(self, name: str, description: str) -> dict[str, Any]:
        if any(a["name"] == name for a in self.agents.values()):
            raise ValueError("agent name already exists")
        row = {"id": new_id(), "name": name, "description": description, "created_at": _now()}
        self.agents[row["id"]] = row
        return row

    def list_agents(self) -> list[dict[str, Any]]:
        return sorted(self.agents.values(), key=lambda r: r["created_at"])

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self.agents.get(agent_id)

    # runs
    def create_run(self, agent_id: str | None, objective: str) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "agent_id": agent_id,
            "objective": objective,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.runs[row["id"]] = row
        return row

    def list_runs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [r for r in self.runs.values() if status is None or r["status"] == status]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def set_run_status(self, run_id: str, status: str) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        if row is None:
            return None
        row["status"] = status
        row["updated_at"] = _now()
        return row

    # prds
    def create_prd(self, source_key: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "source_key": source_key,
            "payload": payload,
            "status": "draft",
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.prds[row["id"]] = row
        return row

    def list_prds(self, limit: int = 50) -> list[dict[str, Any]]:
        return sorted(self.prds.values(), key=lambda r: r["created_at"], reverse=True)[:limit]

    def get_prd(self, prd_id: str) -> dict[str, Any] | None:
        return self.prds.get(prd_id)

    def set_prd_status(self, prd_id: str, status: str) -> dict[str, Any] | None:
        row = self.prds.get(prd_id)
        if row is None:
            return None
        row["status"] = status
        row["updated_at"] = _now()
        row["payload"] = {**row["payload"], "status": status}
        return row

    # agent specifications (Sprint 2)
    def create_agent_spec(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "slug": slug,
            "status": "draft",
            "payload": payload,
            "agent_id": None,
            "approved_by": None,
            "approved_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.agent_specs[row["id"]] = row
        return row

    def list_agent_specs(self, limit: int = 50) -> list[dict[str, Any]]:
        return sorted(self.agent_specs.values(), key=lambda r: r["created_at"], reverse=True)[:limit]

    def get_agent_spec(self, spec_id: str) -> dict[str, Any] | None:
        return self.agent_specs.get(spec_id)

    def approve_agent_spec(self, spec_id: str, agent_id: str, approved_by: str) -> dict[str, Any] | None:
        row = self.agent_specs.get(spec_id)
        if row is None:
            return None
        row["status"] = "approved"
        row["agent_id"] = agent_id
        row["approved_by"] = approved_by
        row["approved_at"] = _now()
        row["updated_at"] = _now()
        row["payload"] = {**row["payload"], "status": "approved"}
        return row

    # plans (SCRUM-27, SCRUM-37, SCRUM-38)
    def create_plan(self, issue_key: str, payload: dict[str, Any], workflow_id: str | None) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "issue_key": issue_key,
            "status": "draft",
            "payload": payload,
            "workflow_id": workflow_id,
            "pull_request_url": None,
            "approved_by": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.plans[row["id"]] = row
        return row

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        return self.plans.get(plan_id)

    def list_plans(self, issue_key: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [p for p in self.plans.values() if issue_key is None or p["issue_key"] == issue_key]
        return sorted(rows, key=lambda p: p["created_at"], reverse=True)[:limit]

    def set_plan(
        self,
        plan_id: str,
        *,
        status: str,
        actor: str | None = None,
        pull_request_url: str | None = None,
    ) -> dict[str, Any] | None:
        row = self.plans.get(plan_id)
        if row is None:
            return None
        row["status"] = status
        row["updated_at"] = _now()
        if actor and status == "approved":
            row["approved_by"] = actor
        if pull_request_url:
            row["pull_request_url"] = pull_request_url
        return row

    # evaluations (SCRUM-28, SCRUM-39)
    def create_evaluation(
        self,
        *,
        subject: str,
        dataset: str,
        score: float,
        threshold: float,
        passed: bool,
        critical_failures: list[str],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        row = {
            "id": len(self.evaluations) + 1,
            "subject": subject,
            "dataset": dataset,
            "score": round(float(score), 4),
            "threshold": round(float(threshold), 4),
            "passed": passed,
            "critical_failures": critical_failures,
            "results": results,
            "review_outcome": None,
            "review_actor": None,
            "review_note": "",
            "created_at": _now(),
        }
        self.evaluations.append(row)
        return row

    def get_evaluation(self, evaluation_id: int) -> dict[str, Any] | None:
        for row in self.evaluations:
            if row["id"] == evaluation_id:
                return row
        return None

    def list_evaluations(self, subject: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [e for e in self.evaluations if subject is None or e["subject"] == subject]
        return sorted(rows, key=lambda e: e["id"], reverse=True)[:limit]

    def review_evaluation(self, evaluation_id: int, *, decision: str, actor: str, note: str) -> dict[str, Any] | None:
        row = self.get_evaluation(evaluation_id)
        if row is None:
            return None
        row.update(review_outcome=decision, review_actor=actor, review_note=note)
        return row

    def latest_evaluation(self, subject: str) -> dict[str, Any] | None:
        rows = [e for e in self.evaluations if e["subject"] == subject]
        return max(rows, key=lambda e: e["id"]) if rows else None

    # workflows (SCRUM-23, SCRUM-33)
    def create_workflow(self, definition: str, state: dict[str, Any], nodes: int) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "definition": definition,
            "status": "running",
            "current_step": None,
            "state": state,
            "node_count": nodes,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.workflows[row["id"]] = row
        return row

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        return self.workflows.get(workflow_id)

    def list_workflows(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [r for r in self.workflows.values() if status is None or r["status"] == status]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]

    def set_workflow(
        self, workflow_id: str, *, status: str, current_step: str | None, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        row = self.workflows.get(workflow_id)
        if row is None:
            return None
        row.update(status=status, current_step=current_step, state=state, updated_at=_now())
        return row

    def add_checkpoint(
        self,
        workflow_id: str,
        *,
        step: str,
        kind: str,
        state: dict[str, Any],
        decision: str | None,
        actor: str | None,
        note: str = "",
    ) -> dict[str, Any]:
        seq = 1 + max((c["seq"] for c in self.checkpoints if c["workflow_id"] == workflow_id), default=0)
        row = {
            "id": len(self.checkpoints) + 1,
            "workflow_id": workflow_id,
            "seq": seq,
            "step": step,
            "kind": kind,
            "state": state,
            "decision": decision,
            "actor": actor,
            "note": note,
            "at": _now(),
        }
        self.checkpoints.append(row)
        return row

    def list_checkpoints(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = [c for c in self.checkpoints if c["workflow_id"] == workflow_id]
        return sorted(rows, key=lambda c: c["seq"])

    # audit
    def add_audit(self, actor: str, action: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": len(self.audit) + 1,
            "at": _now(),
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
        }
        self.audit.append(row)
        return row

    def list_audit(self, entity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = [r for r in self.audit if entity_id is None or r["entity_id"] == entity_id]
        return sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]

    def audit_count(self) -> int:
        return len(self.audit)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.runs.values():
            out[row["status"]] = out.get(row["status"], 0) + 1
        return out

    # artifacts and releases (SCRUM-47, SCRUM-48, SCRUM-49)
    def create_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        row = dict(artifact)
        row["created_at"] = _now()
        self.artifacts[row["commit_sha"]] = row
        return row

    def get_artifact(self, commit_sha: str) -> dict[str, Any] | None:
        return self.artifacts.get((commit_sha or "").lower())

    def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        return sorted(self.artifacts.values(), key=lambda r: r["created_at"], reverse=True)[:limit]

    def create_release(self, *, version: str, commit_sha: str, target: str, report: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": new_id(),
            "version": version,
            "commit_sha": commit_sha,
            "target": target,
            "status": "draft",
            "report": report,
            "notes": "",
            "approved_by": None,
            "published_at": None,
            "rolled_back_to": None,
            "rollback_reason": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.releases[row["id"]] = row
        return row

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        return self.releases.get(release_id)

    def list_releases(self, commit_sha: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [r for r in self.releases.values() if commit_sha is None or r["commit_sha"] == commit_sha]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]

    def set_release(self, release_id: str, **fields: Any) -> dict[str, Any] | None:
        row = self.releases.get(release_id)
        if row is None:
            return None
        row.update({k: v for k, v in fields.items() if v is not None})
        row["updated_at"] = _now()
        return row


class PgStore:
    """PostgreSQL store. One short-lived connection per operation."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._psycopg = None

    def _connect(self):
        if self._psycopg is None:
            import psycopg
            from psycopg.rows import dict_row

            self._psycopg = (psycopg, dict_row)
        psycopg, dict_row = self._psycopg
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def init(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)
            conn.commit()

    def close(self) -> None:
        return None

    def create_agent(self, name: str, description: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO agents (id, name, description) VALUES (%s, %s, %s) "
                    "RETURNING id, name, description, created_at",
                    (new_id(), name, description),
                )
            except Exception as exc:  # unique violation surfaces as 409 in the API
                conn.rollback()
                if "duplicate key" in str(exc):
                    raise ValueError("agent name already exists") from exc
                raise
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, name, description, created_at FROM agents ORDER BY created_at")
            return [_serialise(r) for r in cur.fetchall()]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, created_at FROM agents WHERE id = %s", (agent_id,)
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def create_run(self, agent_id: str | None, objective: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (id, agent_id, objective, status) VALUES (%s, %s, %s, 'queued') "
                "RETURNING id, agent_id, objective, status, created_at, updated_at",
                (new_id(), agent_id, objective),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_runs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT id, agent_id, objective, status, created_at, updated_at FROM runs "
                    "WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT id, agent_id, objective, status, created_at, updated_at FROM runs "
                    "ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            return [_serialise(r) for r in cur.fetchall()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, agent_id, objective, status, created_at, updated_at FROM runs WHERE id = %s",
                (run_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def set_run_status(self, run_id: str, status: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status = %s, updated_at = now() WHERE id = %s "
                "RETURNING id, agent_id, objective, status, created_at, updated_at",
                (status, run_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    # agent specifications (Sprint 2)
    def create_agent_spec(self, slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_specs (id, slug, payload) VALUES (%s, %s, %s::jsonb) "
                "RETURNING id, slug, status, payload, agent_id, approved_by, approved_at, created_at, updated_at",
                (new_id(), slug, _json.dumps(payload)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_agent_specs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, status, payload, agent_id, approved_by, approved_at, created_at, updated_at "
                "FROM agent_specs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def get_agent_spec(self, spec_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, slug, status, payload, agent_id, approved_by, approved_at, created_at, updated_at "
                "FROM agent_specs WHERE id = %s",
                (spec_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def approve_agent_spec(self, spec_id: str, agent_id: str, approved_by: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_specs SET status = 'approved', agent_id = %s, approved_by = %s, "
                "approved_at = now(), updated_at = now(), "
                "payload = jsonb_set(payload, '{status}', '\"approved\"'::jsonb) WHERE id = %s "
                "RETURNING id, slug, status, payload, agent_id, approved_by, approved_at, created_at, updated_at",
                (agent_id, approved_by, spec_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    def add_audit(self, actor: str, action: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_events (actor, action, entity_type, entity_id, payload) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id, at, actor, action, entity_type, entity_id, payload",
                (actor, action, entity_type, entity_id, _json.dumps(payload)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_audit(self, entity_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            if entity_id:
                cur.execute(
                    "SELECT id, at, actor, action, entity_type, entity_id, payload FROM audit_events "
                    "WHERE entity_id = %s ORDER BY id DESC LIMIT %s",
                    (entity_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, at, actor, action, entity_type, entity_id, payload FROM audit_events "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            return [_serialise(r) for r in cur.fetchall()]

    # prds
    def create_prd(self, source_key: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prds (id, source_key, payload) VALUES (%s, %s, %s::jsonb) "
                "RETURNING id, source_key, payload, status, created_at, updated_at",
                (new_id(), source_key, _json.dumps(payload)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_prds(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, source_key, payload, status, created_at, updated_at FROM prds "
                "ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def get_prd(self, prd_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, source_key, payload, status, created_at, updated_at FROM prds WHERE id = %s",
                (prd_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def set_prd_status(self, prd_id: str, status: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE prds SET status = %s, updated_at = now(), "
                "payload = jsonb_set(payload, '{status}', to_jsonb(%s::text)) WHERE id = %s "
                "RETURNING id, source_key, payload, status, created_at, updated_at",
                (status, status, prd_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    # plans (SCRUM-27, SCRUM-37, SCRUM-38)
    def create_plan(self, issue_key: str, payload: dict[str, Any], workflow_id: str | None) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plans (id, issue_key, payload, workflow_id) VALUES (%s, %s, %s::jsonb, %s) "
                "RETURNING id, issue_key, status, payload, workflow_id, pull_request_url, approved_by, created_at, updated_at",
                (new_id(), issue_key, _json.dumps(payload), workflow_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, issue_key, status, payload, workflow_id, pull_request_url, approved_by, created_at, updated_at "
                "FROM plans WHERE id = %s",
                (plan_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def list_plans(self, issue_key: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, issue_key, status, payload, workflow_id, pull_request_url, approved_by, created_at, updated_at "
                "FROM plans WHERE (%s::text IS NULL OR issue_key = %s) ORDER BY created_at DESC LIMIT %s",
                (issue_key, issue_key, limit),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def set_plan(
        self,
        plan_id: str,
        *,
        status: str,
        actor: str | None = None,
        pull_request_url: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE plans SET status = %s, "
                "approved_by = coalesce(%s, approved_by), "
                "pull_request_url = coalesce(%s, pull_request_url), "
                "updated_at = now() WHERE id = %s "
                "RETURNING id, issue_key, status, payload, workflow_id, pull_request_url, approved_by, created_at, updated_at",
                (status, actor if status == "approved" else None, pull_request_url, plan_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    # evaluations (SCRUM-28, SCRUM-39)
    def create_evaluation(
        self,
        *,
        subject: str,
        dataset: str,
        score: float,
        threshold: float,
        passed: bool,
        critical_failures: list[str],
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evaluations (subject, dataset, score, threshold, passed, critical_failures, results) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) "
                "RETURNING id, subject, dataset, score, threshold, passed, critical_failures, results, "
                "review_outcome, review_actor, review_note, created_at",
                (subject, dataset, score, threshold, passed, _json.dumps(critical_failures), _json.dumps(results)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def get_evaluation(self, evaluation_id: int) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, subject, dataset, score, threshold, passed, critical_failures, results, "
                "review_outcome, review_actor, review_note, created_at FROM evaluations WHERE id = %s",
                (evaluation_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def list_evaluations(self, subject: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, subject, dataset, score, threshold, passed, critical_failures, results, "
                "review_outcome, review_actor, review_note, created_at FROM evaluations "
                "WHERE (%s::text IS NULL OR subject = %s::text) ORDER BY id DESC LIMIT %s",
                (subject, subject, limit),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def review_evaluation(self, evaluation_id: int, *, decision: str, actor: str, note: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE evaluations SET review_outcome = %s, review_actor = %s, review_note = %s WHERE id = %s "
                "RETURNING id, subject, dataset, score, threshold, passed, critical_failures, results, "
                "review_outcome, review_actor, review_note, created_at",
                (decision, actor, note, evaluation_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    def latest_evaluation(self, subject: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, subject, dataset, score, threshold, passed, critical_failures, results, "
                "review_outcome, review_actor, review_note, created_at FROM evaluations "
                "WHERE subject = %s::text ORDER BY id DESC LIMIT 1",
                (subject,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    # workflows (SCRUM-23, SCRUM-33)
    def create_workflow(self, definition: str, state: dict[str, Any], nodes: int) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workflows (id, definition, status, node_count, state) "
                "VALUES (%s, %s, 'running', %s, %s::jsonb) "
                "RETURNING id, definition, status, current_step, state, node_count, created_at, updated_at",
                (new_id(), definition, nodes, _json.dumps(state)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, definition, status, current_step, state, node_count, created_at, updated_at "
                "FROM workflows WHERE id = %s",
                (workflow_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def list_workflows(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT id, definition, status, current_step, state, node_count, created_at, updated_at "
                    "FROM workflows WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT id, definition, status, current_step, state, node_count, created_at, updated_at "
                    "FROM workflows ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            return [_serialise(r) for r in cur.fetchall()]

    def set_workflow(
        self, workflow_id: str, *, status: str, current_step: str | None, state: dict[str, Any]
    ) -> dict[str, Any] | None:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE workflows SET status = %s, current_step = %s, state = %s::jsonb, updated_at = now() "
                "WHERE id = %s "
                "RETURNING id, definition, status, current_step, state, node_count, created_at, updated_at",
                (status, current_step, _json.dumps(state), workflow_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None

    def add_checkpoint(
        self,
        workflow_id: str,
        *,
        step: str,
        kind: str,
        state: dict[str, Any],
        decision: str | None,
        actor: str | None,
        note: str = "",
    ) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO checkpoints (workflow_id, seq, step, kind, state, decision, actor, note) "
                "SELECT %s, coalesce(max(seq), 0) + 1, %s, %s, %s::jsonb, %s, %s, %s FROM checkpoints "
                "WHERE workflow_id = %s "
                "RETURNING id, workflow_id, seq, step, kind, state, decision, actor, note, at",
                (workflow_id, step, kind, _json.dumps(state), decision, actor, note, workflow_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def list_checkpoints(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, workflow_id, seq, step, kind, state, decision, actor, note, at "
                "FROM checkpoints WHERE workflow_id = %s ORDER BY seq",
                (workflow_id,),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def audit_count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM audit_events")
            return int(cur.fetchone()["n"])

    def counts(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, count(*) AS n FROM runs GROUP BY status")
            return {r["status"]: int(r["n"]) for r in cur.fetchall()}


    # artifacts and releases (SCRUM-47, SCRUM-48, SCRUM-49)
    def create_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO artifacts (commit_sha, version, workflow, source, image, built_at, tests, stories, signature) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s) "
                "ON CONFLICT (commit_sha) DO NOTHING RETURNING commit_sha, version, workflow, source, image, built_at, "
                "tests, stories, signature, created_at",
                (
                    artifact["commit_sha"],
                    artifact["version"],
                    artifact["workflow"],
                    artifact["source"],
                    artifact["image"],
                    artifact["built_at"],
                    _json.dumps(artifact["tests"]),
                    _json.dumps(artifact["stories"]),
                    artifact["signature"],
                ),
            )
            row = cur.fetchone()
            conn.commit()
            if row is None:
                return self.get_artifact(artifact["commit_sha"]) or {}
            return _serialise(row)

    def get_artifact(self, commit_sha: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT commit_sha, version, workflow, source, image, built_at, tests, stories, signature, created_at "
                "FROM artifacts WHERE commit_sha = %s",
                ((commit_sha or "").lower(),),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def list_artifacts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT commit_sha, version, workflow, source, image, built_at, tests, stories, signature, created_at "
                "FROM artifacts ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def create_release(self, *, version: str, commit_sha: str, target: str, report: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO releases (id, version, commit_sha, target, report) VALUES (%s, %s, %s, %s, %s::jsonb) "
                "RETURNING id, version, commit_sha, target, status, report, notes, approved_by, published_at, "
                "rolled_back_to, rollback_reason, created_at, updated_at",
                (new_id(), version, commit_sha, target, _json.dumps(report)),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row)

    def get_release(self, release_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, version, commit_sha, target, status, report, notes, approved_by, published_at, "
                "rolled_back_to, rollback_reason, created_at, updated_at FROM releases WHERE id = %s::text::uuid",
                (release_id,),
            )
            row = cur.fetchone()
            return _serialise(row) if row else None

    def list_releases(self, commit_sha: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, version, commit_sha, target, status, report, notes, approved_by, published_at, "
                "rolled_back_to, rollback_reason, created_at, updated_at FROM releases "
                "WHERE (%s::text IS NULL OR commit_sha = %s::text) ORDER BY created_at DESC LIMIT %s",
                (commit_sha, commit_sha, limit),
            )
            return [_serialise(r) for r in cur.fetchall()]

    def set_release(self, release_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"status", "notes", "approved_by", "published_at", "rolled_back_to", "rollback_reason"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_release(release_id)
        assignments = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE releases SET {assignments}, updated_at = now() WHERE id = %s::text::uuid "
                "RETURNING id, version, commit_sha, target, status, report, notes, approved_by, published_at, "
                "rolled_back_to, rollback_reason, created_at, updated_at",
                (*values, release_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _serialise(row) if row else None


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[key] = float(value)
        else:
            out[key] = value
    return out


def build_store():
    """Store selected by environment: memory for tests, PostgreSQL otherwise."""
    if os.getenv("FACTORY_STORE", "").lower() == "memory":
        return MemoryStore()
    dsn = os.getenv("FACTORY_DB_DSN") or (
        "host={h} port={p} dbname={d} user={u} password={w}".format(
            h=os.getenv("FACTORY_DB_HOST", "db"),
            p=os.getenv("FACTORY_DB_PORT", "5432"),
            d=os.getenv("FACTORY_DB_NAME", "factory"),
            u=os.getenv("FACTORY_DB_USER", "factory"),
            w=os.getenv("FACTORY_DB_PASSWORD", ""),
        )
    )
    return PgStore(dsn)
