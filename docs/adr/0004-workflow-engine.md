# ADR 0004 — a local checkpointed workflow engine instead of the LangGraph runtime

Status: accepted (Sprint 4, SCRUM-23 / SCRUM-33)

## Context

The stories name LangGraph: a graph of nodes, a checkpoint after each step, an
interrupt before a step that needs a human, and a resume from the checkpoint on
approval. The Factory runs on 1 vCPU and ~1.5 GiB of available RAM next to ten
production containers, and the project forbids new paid surfaces.

## Decision

Implement those semantics locally in `services/api/app/workflow.py`:

- declarative ordered definitions (`DISCOVERY`, `DELIVERY`), each node a pure
  function of the accumulated state;
- a checkpoint written after every executed node, in PostgreSQL;
- an `interrupt` checkpoint and `status = awaiting_approval` before every node
  marked `approval=True`;
- resumption that runs the gated node itself, so approving cannot complete a
  workflow that never produced the artefact the gate protects;
- a `decision` checkpoint recording who decided, when and why.

## Consequences

- No extra runtime, no extra RAM, no provider calls; the whole surface is testable
  with `pytest` and the in-memory store.
- The graph API is the shape LangGraph uses, so a later swap touches `workflow.py`
  and `store.py` only — the HTTP contract and the tests stay.
- We own the semantics: conditional edges, parallel branches and streaming are not
  implemented. Adding them means a new ADR, not a silent extension.

## Exit conditions

Reconsider when a story requires conditional routing, parallel fan-out, durable
timers or replay tooling that would take more code than the dependency. At that
point the criterion is measurable: a `workflow.py` diff larger than the adapter
LangGraph would need.
