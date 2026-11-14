"""Business request chain: one request, every stage, the same human gates.

Before this module a business request only produced a PRD: someone had to read the
discovery backlog, start a delivery workflow by hand, copy ``state.backlog`` into it,
and remember to record a QA evaluation before the release gate would open. Nothing
linked the two runs, so a forgotten copy silently broke the chain.

``start_request`` opens the chain (request id = discovery workflow id) and
``on_decision`` keeps it moving: when a chained workflow *completes*, the next stage
starts by itself and inherits what the previous one computed. The gates do not move —
each stage still parks on its ``approval=True`` node — so this removes copying and
forgetting, not the human decisions.

Every stage is recorded in the workflow ``state["chain"]`` (no schema change): the
discovery run carries ``delivery_workflow_id``, the delivery run carries what the
release stage needs. ``summarize`` rebuilds the whole picture from those two rows.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from . import workflow as engine
from .redaction import redact

STAGE_DISCOVERY = "discovery"
STAGE_DELIVERY = "delivery"
STAGE_RELEASE = "release"


class ChainError(RuntimeError):
    """The request cannot be chained (bad input, broken link, missing backlog)."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chain(row: dict[str, Any]) -> dict[str, Any]:
    return dict((row.get("state") or {}).get("chain") or {})


def _save_chain(store, row: dict[str, Any], chain: dict[str, Any]) -> dict[str, Any]:
    state = {**(row.get("state") or {}), "chain": chain}
    return store.set_workflow(
        row["id"], status=row["status"], current_step=row.get("current_step"), state=state
    ) or row


def _audit(store, actor: str, action: str, entity_id: str, payload: dict[str, Any]) -> None:
    store.add_audit(actor, action, "request", entity_id, redact(payload))


def _mirror(store, text: str, source_key: str | None, actor: str) -> dict[str, Any]:
    """Create (or reuse) the Jira story for this request. Never blocks the request."""
    from .jira_write import JiraWriteDisabled, JiraWriteForbidden, JiraWriter

    try:
        writer = JiraWriter()
        if not (writer.configured and writer.enabled):
            reason = "jira write path is not configured or not enabled"
            _audit(store, actor, "jira.mirror.skipped", "pending", {"reason": reason})
            return {"mirrored": False, "reason": reason}
        created = writer.create_issue(
            summary=text,
            issue_type="Story",
            description=(
                f"{text}\n\nDemande business enregistrée par la chaîne AI Factory.\n"
                f"Source : {source_key or 'n/a'}"
            ),
        )
        return {"mirrored": True, **created}
    except (JiraWriteDisabled, JiraWriteForbidden, ValueError, OSError) as exc:
        return {"mirrored": False, "reason": str(exc)[:200]}


def start_request(
    store,
    text: str,
    *,
    source_key: str | None = None,
    actor: str = "anonymous",
    mirror_jira: bool = False,
) -> dict[str, Any]:
    """Open a chain: the discovery workflow *is* the request."""
    text = " ".join((text or "").split())
    if len(text) < 20:
        raise ChainError("a business request needs at least 20 characters")

    wf = engine.start(store, STAGE_DISCOVERY, {"request": text}, actor=actor)
    chain: dict[str, Any] = {
        "request_id": wf["id"],
        "stage": STAGE_DISCOVERY,
        "request_text": text,
        "source_key": source_key or None,
        "opened_at": _now(),
        "opened_by": actor,
        "delivery_workflow_id": None,
        "release": None,
        "jira": None,
    }
    if mirror_jira:
        chain["jira"] = _mirror(store, text, source_key, actor)
        if chain["jira"].get("mirrored"):
            _audit(store, actor, "jira.mirror.linked", wf["id"], {"jira": chain["jira"].get("key")})
    wf = _save_chain(store, wf, chain)
    _audit(
        store, actor, "request.opened", wf["id"],
        {"source_key": chain["source_key"], "stage": STAGE_DISCOVERY,
         "waiting_for": wf.get("current_step")},
    )
    return {"request_id": wf["id"], "chain": chain, "workflow": wf}


def _start_delivery(store, row: dict[str, Any], chain: dict[str, Any], actor: str) -> dict[str, Any]:
    backlog = (row.get("state") or {}).get("backlog") or {}
    if not backlog.get("items"):
        chain["error"] = "discovery produced no backlog to deliver"
        _save_chain(store, row, chain)
        _audit(store, actor, "chain.blocked", row["id"], {"reason": chain["error"]})
        return {"action": "blocked", "reason": chain["error"]}

    delivery = engine.start(
        store,
        STAGE_DELIVERY,
        {
            "backlog": backlog,
            "chain": {**chain, "stage": STAGE_DELIVERY, "previous_workflow_id": row["id"]},
        },
        actor=actor,
    )
    chain["delivery_workflow_id"] = delivery["id"]
    chain["stage"] = STAGE_DELIVERY
    chain["error"] = None
    _save_chain(store, row, chain)
    _audit(
        store, actor, "chain.delivery.started", row["id"],
        {"delivery_workflow_id": delivery["id"], "backlog_items": len(backlog["items"]),
         "waiting_for": delivery.get("current_step")},
    )
    return {
        "action": "delivery_started",
        "delivery_workflow_id": delivery["id"],
        "waiting_for": delivery.get("current_step"),
    }


def _prepare_release(store, row: dict[str, Any], chain: dict[str, Any], actor: str) -> dict[str, Any]:
    state = row.get("state") or {}
    source_key = chain.get("source_key")
    items = ((state.get("plan") or {}).get("items")) or []
    stories = [
        {
            "key": source_key if index == 0 and source_key else f"{source_key or 'ITEM'}-{index + 1}",
            "summary": item.get("acceptance", ""),
            "status": "Done",
            "type": "Story",
        }
        for index, item in enumerate(items)
    ]
    release = {
        "ready": bool((state.get("release") or {}).get("ready")),
        "workflow_id": row["id"],
        "prepared_at": _now(),
        "stories": stories,
        "qa": {
            "passed": (state.get("qa") or {}).get("passed"),
            "evaluation_id": (state.get("qa") or {}).get("evaluation_id"),
            "score": (state.get("qa") or {}).get("evaluation_score"),
        },
        # Nothing is deployed here: the release chain still needs a real artefact.
        "requires": ["artifact from the ci workflow (commit_sha)", "POST /api/v1/releases/readiness"],
    }
    chain["stage"] = STAGE_RELEASE
    chain["release"] = release
    _save_chain(store, row, chain)
    _audit(
        store, actor, "chain.release.prepared", chain.get("request_id") or row["id"],
        {"delivery_workflow_id": row["id"], "stories": len(stories),
         "qa_passed": release["qa"]["passed"]},
    )
    jira_key = (chain.get("jira") or {}).get("key")
    if jira_key and (chain.get("jira") or {}).get("mirrored"):
        try:
            from .jira_write import JiraWriter

            JiraWriter().comment(
                jira_key,
                f"Delivery terminée (workflow {row['id']}). QA: {'OK' if release['qa']['passed'] else 'KO'}. "
                "Prêt pour la chaîne release : il manque l'artefact CI du commit.",
            )
        except Exception as exc:  # a comment must never break the chain
            _audit(store, actor, "jira.mirror.failed", chain.get("request_id") or row["id"],
                   {"reason": str(exc)[:200]})
    return {"action": "release_prepared", "story_count": len(stories),
            "requires": release["requires"], "jira": jira_key or None}


def on_decision(
    store, row: dict[str, Any], *, decision: str, actor: str
) -> dict[str, Any] | None:
    """Advance the chain after a human decision. Returns what happened, if anything."""
    if decision != "approved":
        return None
    if row.get("status") != "completed":
        return None
    chain = _chain(row)
    if not chain.get("request_id"):
        return None  # manually started workflow: nothing to chain

    definition = row.get("definition")
    if definition == STAGE_DISCOVERY:
        if chain.get("delivery_workflow_id"):
            return {"action": "already_chained", "delivery_workflow_id": chain["delivery_workflow_id"]}
        # Re-read: a replay must not open a second delivery workflow.
        fresh = store.get_workflow(row["id"]) or row
        again = _chain(fresh)
        if again.get("delivery_workflow_id"):
            return {"action": "already_chained", "delivery_workflow_id": again["delivery_workflow_id"]}
        return _start_delivery(store, fresh, again, actor)
    if definition == STAGE_DELIVERY:
        if chain.get("stage") == STAGE_RELEASE:
            return {"action": "release_already_prepared"}
        return _prepare_release(store, row, chain, actor)
    return None


def _waiting(row: dict[str, Any]) -> str | None:
    if row.get("status") == "awaiting_approval":
        return row.get("current_step")
    return None


def _next_action(row: dict[str, Any]) -> str:
    if row.get("status") == "completed":
        return "none — stage terminée"
    if row.get("status") == "cancelled":
        return "none — chaîne annulée"
    step = row.get("current_step") or "?"
    if row.get("status") == "awaiting_approval":
        return f"POST /api/v1/workflows/{row['id']}/decision (porte « {step} »)"
    return f"étape « {step} » en cours"


def summarize(store, request_id: str) -> dict[str, Any]:
    """Rebuild the whole request from its two (or three) workflow rows."""
    row = store.get_workflow(request_id)
    if row is None or row.get("definition") != STAGE_DISCOVERY:
        raise ChainError("request not found")
    chain = _chain(row)
    stages = [{
        "stage": STAGE_DISCOVERY,
        "workflow_id": row["id"],
        "status": row["status"],
        "current_step": row.get("current_step"),
        "waiting_for": _waiting(row),
        "next": _next_action(row),
    }]
    delivery_id = chain.get("delivery_workflow_id")
    delivery = store.get_workflow(delivery_id) if delivery_id else None
    # the release handoff lives on the delivery row: that is where it was computed
    release = _chain(delivery).get("release") if delivery else None
    if delivery:
        stages.append({
            "stage": STAGE_DELIVERY,
            "workflow_id": delivery["id"],
            "status": delivery["status"],
            "current_step": delivery.get("current_step"),
            "waiting_for": _waiting(delivery),
            "next": _next_action(delivery),
        })
    if release:
        stages.append({
            "stage": STAGE_RELEASE,
            "status": "prepared" if release.get("ready") else "blocked",
            "workflow_id": delivery_id,
            "waiting_for": None,
            "next": "POST /api/v1/artifacts (CI) puis /api/v1/releases/readiness",
        })
    audit = list(store.list_audit(entity_id=row["id"], limit=100))
    if delivery_id:
        audit += list(store.list_audit(entity_id=delivery_id, limit=100))
    audit.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "request_id": row["id"],
        "request": chain.get("request_text") or (row.get("state") or {}).get("request"),
        "source_key": chain.get("source_key"),
        "opened_at": chain.get("opened_at"),
        "opened_by": chain.get("opened_by"),
        "stage": STAGE_RELEASE if release else (STAGE_DELIVERY if delivery else STAGE_DISCOVERY),
        "stages": stages,
        "jira": chain.get("jira"),
        "release": release,
        "error": chain.get("error"),
        "audit": audit[:200],
        "audit_count": len(audit),
    }


def jira_writes_available() -> bool:
    """True only when the deployment actually opened the write path."""
    return os.getenv("JIRA_WRITE_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}
