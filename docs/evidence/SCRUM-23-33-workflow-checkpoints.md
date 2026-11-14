# SCRUM-23 / SCRUM-33 — checkpointed workflow and human approval (Sprint 4)

Date: 2026-11-14 · Branch `feature/sp4-workflow-checkpoints` · Chain: DEV → Qualité → Production

## What the stories asked for

- **SCRUM-23** — build the initial graph with a persisted checkpoint and a human
  approval interrupt/resume; unit tests for node/state, integration test for the
  checkpoint, security tool boundaries, end-to-end request-to-approved workflow.
- **SCRUM-33** — as a workflow owner I can run and approve a checkpointed
  workflow: initial execution, checkpoint persistence, approval interrupt and resume.

## What was built

| Piece | File |
| --- | --- |
| Graph definitions, engine, interrupt/resume | `services/api/app/workflow.py` |
| HTTP surface (`/api/v1/workflows`) | `services/api/app/api_workflow.py` |
| Persistence (`workflows`, `checkpoints`) | `services/api/app/store.py` (both stores) |
| Contract tests (12) | `tests/test_workflow.py` |

Definitions are declarative ordered node lists:

- `discovery`: `intake → prd_draft → architecture_review` **(gate)** `→ backlog_plan`
- `delivery`: `plan → implement` **(gate)** `→ qa → release_ready` **(gate)**

A node is a pure function of the accumulated state. The engine writes a
checkpoint after every node, and stops **before** any node marked `approval=True`
— so the PRD is already produced and persisted when the workflow waits for the
architect. `POST /workflows/{id}/decision` with `approved` resumes *at that node*,
`rejected` cancels the workflow; both are recorded as a `decision` checkpoint with
the actor name and the note, and audited (`workflow.started`,
`workflow.interrupted`, `workflow.approved|rejected`, `workflow.completed`).

Any other transition answers **409** — a completed or cancelled workflow cannot be
approved a second time, which is what makes the gate trustworthy.

## Proof — unit and integration

```
$ .venv/bin/python -m pytest -q
85 passed in 0.54s

$ .venv/bin/python scripts/secret_scan.py .
secret scan: ok
```

The 12 new tests cover: declared gates, stopping before the gate, resuming from the
checkpoint, rejection never running the next node, 409 on a second decision, 404 on
an unknown workflow, 422 on an unknown definition or a request too short to become a
PRD, the two gates of `delivery` in order, refusal of a delivery with no backlog,
the audit trail, and checkpoint snapshots (a checkpoint keeps the state *that step*
saw, not the final one).

## Proof — real PostgreSQL, not only the in-memory store

A throwaway `postgres:16-alpine` (256 MiB, bound to `127.0.0.1:55432`, removed
afterwards) ran `store.init()` then a full discovery workflow:

```
store PgStore
init ok
started awaiting_approval architecture_review
after approve completed done backlog 4
checkpoints [(1,'intake','node',None), (2,'prd_draft','node',None),
             (3,'architecture_review','interrupt',None),
             (4,'architecture_review','decision','approved'),
             (5,'architecture_review','node',None), (6,'backlog_plan','node',None)]
audit_count 4
reloaded completed 6
```

The last line re-opens a **new** store against the same database and still reads the
completed workflow and its six checkpoints: the checkpoint is persisted, not cached.

## Security tool boundaries

No node calls a provider, a tool or the network: the engine is deterministic and the
only I/O is the store. There is therefore no tool-boundary surface to sandbox yet —
when tools are added (Sprint 5+), they must run behind `tools/` with an explicit
allow-list, exactly as the Jira client is today (read-only, `GET` only, asserted by
`tests/test_jira_integration.py`).

## Deliberate scope

`langgraph` itself is not a dependency: it pulls a runtime the 1 vCPU box does not
need, and the semantics the stories require — ordered nodes, interrupt before a
gate, persisted checkpoint, resumption — are what the tests above pin down. The
decision and its exit conditions are recorded in `docs/adr/0004-workflow-engine.md`.
