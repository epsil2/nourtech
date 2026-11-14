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
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_events (entity_type, entity_id);
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

    def audit_count(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM audit_events")
            return int(cur.fetchone()["n"])

    def counts(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, count(*) AS n FROM runs GROUP BY status")
            return {r["status"]: int(r["n"]) for r in cur.fetchall()}


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
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
