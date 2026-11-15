"""Control-plane persistence primitives (Phase 2 of the control-plane migration).

The AI Factory store keeps typed tables for the entities that existed before this
migration (agents, specs, runs, releases, audit).  The control-plane entities added
here — projects, proposals, approvals, executions, execution events, deployments,
metrics snapshots — are stored as JSON documents under a single ``cp_documents``
table with a ``kind`` discriminator.

Why documents instead of one table per entity:

* every entity in the target model is versioned and grows new fields per sprint
  (Hermes evidence, Jira links, GitHub artefacts), so a wide schema would need a
  migration per sprint on a 1 vCPU host;
* the API surfaces them as JSON anyway;
* the relationships that must be *enforced* (uniqueness of a Hermes task id, of a
  Jira issue key, of a deployment per release/environment) are enforced in the
  service layer and are covered by tests.

The trade-off is explicit: no cross-entity foreign keys.  When the model settles,
these documents get promoted to typed tables (see docs/evidence/phase 1, section H).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

CP_SCHEMA = """
CREATE TABLE IF NOT EXISTS cp_documents (
    seq         bigserial PRIMARY KEY,
    kind        text NOT NULL,
    id          uuid NOT NULL,
    project_id  uuid NULL,
    status      text NOT NULL DEFAULT '',
    doc         jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, id)
);
CREATE INDEX IF NOT EXISTS cp_documents_kind_idx ON cp_documents (kind, created_at DESC);
CREATE INDEX IF NOT EXISTS cp_documents_project_idx ON cp_documents (project_id, kind);
CREATE INDEX IF NOT EXISTS cp_documents_doc_idx ON cp_documents USING gin (doc jsonb_path_ops);
"""


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_out(row: dict[str, Any]) -> dict[str, Any]:
    doc = dict(row.get("doc") or {})
    doc["id"] = str(row["id"])
    doc["seq"] = row.get("seq")
    doc.setdefault("created_at", _iso(row.get("created_at")))
    doc["updated_at"] = _iso(row.get("updated_at")) or doc.get("updated_at")
    if row.get("project_id"):
        doc.setdefault("project_id", str(row["project_id"]))
    if row.get("status"):
        doc.setdefault("status", row["status"])
    return doc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlPlaneMixin:
    """Document store shared by the in-memory and PostgreSQL stores."""

    def _connect(self):  # pragma: no cover - implemented by PgStore
        raise NotImplementedError("_connect is only available on the PostgreSQL store")

    # ---- PostgreSQL -------------------------------------------------------
    def _cp_pg(self) -> bool:
        return hasattr(self, "dsn")

    def cp_save(
        self,
        kind: str,
        doc: dict[str, Any],
        *,
        project_id: str | None = None,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        ident = str(doc_id or doc.get("id") or uuid.uuid4())
        payload = dict(doc)
        payload.pop("seq", None)
        payload.pop("updated_at", None)
        payload.setdefault("created_at", _now())
        status = str(payload.get("status") or "")
        project_id = project_id or payload.get("project_id")

        if self._cp_pg():
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO cp_documents (kind, id, project_id, status, doc) "
                    "VALUES (%s, %s::uuid, %s::uuid, %s, %s::jsonb) "
                    "ON CONFLICT (kind, id) DO UPDATE "
                    "SET doc = EXCLUDED.doc, status = EXCLUDED.status, "
                    "project_id = COALESCE(EXCLUDED.project_id, cp_documents.project_id), "
                    "updated_at = now() "
                    "RETURNING seq, kind, id, project_id, status, doc, created_at, updated_at",
                    (kind, ident, project_id, status, json.dumps(payload, default=str)),
                )
                row = cur.fetchone()
                conn.commit()
            return _row_out(dict(row))

        docs = self._cp_docs()
        key = (kind, ident)
        previous = docs.get(key)
        record = {
            "seq": previous["seq"] if previous else len(docs) + 1,
            "kind": kind,
            "id": ident,
            "project_id": project_id,
            "status": status,
            "doc": payload,
            "created_at": previous["created_at"] if previous else _now(),
            "updated_at": _now(),
        }
        docs[key] = record
        return _row_out(record)

    def cp_get(self, kind: str, doc_id: str) -> dict[str, Any] | None:
        if self._cp_pg():
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, kind, id, project_id, status, doc, created_at, updated_at "
                    "FROM cp_documents WHERE kind = %s AND id = %s::uuid",
                    (kind, str(doc_id)),
                )
                row = cur.fetchone()
            return _row_out(dict(row)) if row else None

        record = self._cp_docs().get((kind, str(doc_id)))
        return _row_out(record) if record else None

    def cp_patch(self, kind: str, doc_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        current = self.cp_get(kind, doc_id)
        if current is None:
            return None
        merged = dict(current, **fields)
        return self.cp_save(
            kind,
            merged,
            project_id=merged.get("project_id"),
            doc_id=str(doc_id),
        )

    def cp_list(
        self,
        kind: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        where: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self._cp_pg():
            clauses = ["kind = %s"]
            params: list[Any] = [kind]
            if project_id:
                clauses.append("project_id = %s::uuid")
                params.append(project_id)
            if status:
                clauses.append("status = %s")
                params.append(status)
            if where:
                clauses.append("doc @> %s::jsonb")
                params.append(json.dumps(where, default=str))
            params.append(limit)
            sql = (
                "SELECT seq, kind, id, project_id, status, doc, created_at, updated_at "
                f"FROM cp_documents WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, seq DESC LIMIT %s"
            )
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return [_row_out(dict(r)) for r in cur.fetchall()]

        rows = [
            _row_out(record)
            for (record_kind, _), record in self._cp_docs().items()
            if record_kind == kind
            and (not project_id or record.get("project_id") == project_id)
            and (not status or record.get("status") == status)
            and (not where or all(record["doc"].get(k) == v for k, v in where.items()))
        ]
        rows.sort(key=lambda d: (d.get("created_at") or "", d.get("seq") or 0), reverse=True)
        return rows[:limit]

    def cp_delete(self, kind: str, doc_id: str) -> bool:
        if self._cp_pg():
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM cp_documents WHERE kind = %s AND id = %s::uuid", (kind, str(doc_id)))
                deleted = cur.rowcount > 0
                conn.commit()
            return deleted
        return self._cp_docs().pop((kind, str(doc_id)), None) is not None

    # ---- helpers ----------------------------------------------------------
    def _cp_docs(self) -> dict[tuple[str, str], dict[str, Any]]:
        docs = getattr(self, "_cp_documents", None)
        if docs is None:
            docs = {}
            self._cp_documents = docs
        return docs
