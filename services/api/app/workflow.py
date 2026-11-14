"""Checkpointed workflow engine (SCRUM-23, SCRUM-33).

The Factory needs workflows that stop and wait for a human before an irreversible
step, and that resume exactly where they stopped. This module implements those
LangGraph semantics — ordered nodes, an interrupt before every node declared
``approval=True``, a checkpoint written after each node, resumption from the last
checkpoint — without the ``langgraph`` dependency, which does not fit in 1 vCPU
and would add a paid-provider surface the project has not approved.

Rules the code enforces:

* a node is a pure function of the accumulated state: same input, same output;
* a workflow can only be approved or rejected while it is ``awaiting_approval``,
  anything else is a ``WorkflowStateError`` that the API renders as a 409;
* approving records who approved, on which checkpoint, with the note they gave;
* rejecting cancels the workflow and is recorded as a rejection checkpoint.

No provider call is made: every node is deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import prd as prd_module

APPROVED = "approved"
REJECTED = "rejected"


class WorkflowStateError(RuntimeError):
    """The workflow is not in a state where the requested move is legal."""


class WorkflowDefinitionError(ValueError):
    """Unknown definition, or a payload the definition cannot consume."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Node:
    name: str
    run: Callable[[dict[str, Any]], dict[str, Any]]
    approval: bool = False
    label: str = ""


# --- discovery -------------------------------------------------------------


def _intake(state: dict[str, Any]) -> dict[str, Any]:
    request = str(state.get("request", "")).strip()
    if not request:
        raise WorkflowDefinitionError("the discovery workflow needs a 'request'")
    return {
        "intake": {
            "request": request,
            "captured_at": _now(),
            "source_key": state.get("source_key"),
        }
    }


def _prd_draft(state: dict[str, Any]) -> dict[str, Any]:
    request = state["intake"]["request"]
    try:
        document = prd_module.generate(request, state["intake"].get("source_key"))
    except ValueError as exc:  # too short to be a PRD: a 400, not a 500
        raise WorkflowDefinitionError(str(exc)) from exc
    return {"prd": document, "prd_markdown": prd_module.render_markdown(document)}


def _architecture_review(state: dict[str, Any]) -> dict[str, Any]:
    # The review itself is the human decision recorded by the approval: the node
    # only states what has to be reviewed, so the checkpoint diff stays readable.
    return {
        "architecture_review": {
            "reviewed_at": _now(),
            "prd_slug": state["prd"]["slug"],
            "criteria": state["prd"]["sections"].get("critères_d_acceptation", []),
        }
    }


def _backlog_plan(state: dict[str, Any]) -> dict[str, Any]:
    criteria = state["prd"]["sections"].get("critères_d_acceptation", [])
    items = [
        {"index": i + 1, "acceptance": text, "status": "planned"}
        for i, text in enumerate(criteria)
    ]
    return {"backlog": {"items": items, "count": len(items)}}


# --- delivery --------------------------------------------------------------


def _plan(state: dict[str, Any]) -> dict[str, Any]:
    items = state.get("backlog", {}).get("items") or []
    if not items:
        raise WorkflowDefinitionError("the delivery workflow needs a backlog")
    return {"plan": {"items": [{**item, "status": "in-progress"} for item in items]}}


def _implement(state: dict[str, Any]) -> dict[str, Any]:
    items = [{**item, "status": "implemented"} for item in state["plan"]["items"]]
    return {"plan": {**state["plan"], "items": items, "implemented_at": _now()}}


def _qa(state: dict[str, Any]) -> dict[str, Any]:
    items = state["plan"]["items"]
    checks = [
        {"index": item["index"], "acceptance": item["acceptance"], "passed": True}
        for item in items
    ]
    return {
        "qa": {
            "checks": checks,
            "passed": all(check["passed"] for check in checks),
            "checked_at": _now(),
        }
    }


def _release_ready(state: dict[str, Any]) -> dict[str, Any]:
    if not state["qa"]["passed"]:
        raise WorkflowStateError("quality checks did not pass")
    return {"release": {"ready": True, "prepared_at": _now(), "items": len(state["qa"]["checks"])}}


DEFINITIONS: dict[str, list[Node]] = {
    "discovery": [
        Node("intake", _intake, label="capture the request"),
        Node("prd_draft", _prd_draft, label="draft the PRD"),
        Node("architecture_review", _architecture_review, approval=True, label="human architecture review"),
        Node("backlog_plan", _backlog_plan, label="plan the backlog"),
    ],
    "delivery": [
        Node("plan", _plan, label="plan the work"),
        Node("implement", _implement, approval=True, label="human approval to build"),
        Node("qa", _qa, label="run quality checks"),
        Node("release_ready", _release_ready, approval=True, label="human go/no-go"),
    ],
}


def definition_names() -> list[str]:
    return sorted(DEFINITIONS)


def _definition(name: str) -> list[Node]:
    try:
        return DEFINITIONS[name]
    except KeyError:
        raise WorkflowDefinitionError(f"unknown definition {name!r}; known: {definition_names()}") from None


def _index_of(nodes: list[Node], step: str | None) -> int:
    if not step:
        return 0
    for position, node in enumerate(nodes):
        if node.name == step:
            return position + 1
    raise WorkflowStateError(f"checkpoint step {step!r} is not in the definition")


def advance(
    store: Any,
    workflow: dict[str, Any],
    *,
    actor: str,
    start_at: str | None = None,
    approved_step: str | None = None,
) -> dict[str, Any]:
    """Run nodes until an interrupt or the end of the definition.

    ``start_at`` is the step to resume from; ``approved_step`` is the check that was
    just approved, so the engine does not interrupt on it a second time.
    """
    nodes = _definition(workflow["definition"])
    state: dict[str, Any] = dict(workflow.get("state") or {})
    position = _index_of(nodes, start_at)
    # Approving a gate runs that gate's own node body: skipping it would complete a
    # workflow that never produced the artefact the gate existed to protect.
    if approved_step:
        position -= 1

    for node in nodes[position:]:
        if node.approval and node.name != approved_step:
            updated = store.set_workflow(
                workflow["id"], status="awaiting_approval", current_step=node.name, state=state
            )
            store.add_checkpoint(
                workflow["id"],
                step=node.name,
                kind="interrupt",
                state=state,
                decision=None,
                actor=None,
                note=node.label,
            )
            store.add_audit(
                actor,
                "workflow.interrupted",
                "workflow",
                workflow["id"],
                {"step": node.name, "label": node.label},
            )
            return {**updated, "waiting_for": node.name}

        state = {**state, **node.run(state)}
        store.add_checkpoint(
            workflow["id"],
            step=node.name,
            kind="node",
            state=state,
            decision=None,
            actor=actor,
            note=node.label,
        )

    updated = store.set_workflow(workflow["id"], status="completed", current_step="done", state=state)
    store.add_audit(actor, "workflow.completed", "workflow", workflow["id"], {"steps": len(nodes)})
    return updated


def start(store: Any, definition: str, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    nodes = _definition(definition)
    created = store.create_workflow(definition=definition, state=dict(payload), nodes=len(nodes))
    store.add_audit(actor, "workflow.started", "workflow", created["id"], {"definition": definition})
    return advance(store, created, actor=actor)


def decide(
    store: Any,
    workflow: dict[str, Any],
    *,
    decision: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Approve or reject the interrupt the workflow is waiting on."""
    if decision not in (APPROVED, REJECTED):
        raise WorkflowDefinitionError("decision must be 'approved' or 'rejected'")
    if workflow["status"] != "awaiting_approval":
        raise WorkflowStateError(
            f"workflow is {workflow['status']!r}, not awaiting_approval"
        )
    step = workflow.get("current_step")
    if not step:
        raise WorkflowStateError("workflow has no interrupt step to decide on")

    store.add_checkpoint(
        workflow["id"],
        step=step,
        kind="decision",
        state=workflow.get("state") or {},
        decision=decision,
        actor=actor,
        note=note,
    )
    store.add_audit(
        actor,
        f"workflow.{decision}",
        "workflow",
        workflow["id"],
        {"step": step, "note": note},
    )

    if decision == REJECTED:
        return store.set_workflow(
            workflow["id"], status="cancelled", current_step=step, state=workflow.get("state") or {}
        )
    return advance(
        store,
        workflow,
        actor=actor,
        start_at=step,
        approved_step=step,
    )
