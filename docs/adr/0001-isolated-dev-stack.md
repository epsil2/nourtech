# ADR 0001 — Isolated DEV stack on a shared 1 vCPU host

- Status: accepted
- Date: 2026-11-14
- Jira: SCRUM-19, parent epic SCRUM-5

## Context

The VPS `srv2115787` already runs five production Compose projects (Hermes,
nourredine.tech, trading-platform, vibe-trading, Traefik). Capacity measured
read-only: **1 vCPU, 3915 MiB RAM (~1.4–1.8 GiB available), 19 GiB free disk**.
Starting a factory stack inside an existing project would make rollback and
ownership ambiguous, and a memory spike could evict a production container.

## Decision

1. The factory gets its own Compose project, `agent-factory-dev`, rooted at
   `deploy/dev/`, with its own network and volume namespace.
2. The service network is `internal: true`. Nothing is published on the host, so
   PostgreSQL (5432) and Redis (6379) are unreachable from outside the network —
   this also removes any need for host firewall rules.
3. Memory ceilings are fixed by the compose file, not by Docker defaults:
   db 320 MiB, cache 96 MiB, api 256 MiB — **672 MiB worst case**, leaving the
   production projects their current headroom.
4. PostgreSQL tuning is set for a small host (`shared_buffers=64MB`,
   `max_connections=25`), Redis runs with `maxmemory 64mb` and persistence
   disabled (cache only, not a system of record).
5. The API container runs as uid 10001 and every service sets
   `no-new-privileges:true`.
6. Logs are capped (10 MiB × 3) so a runaway agent run cannot fill the disk.

## Consequences

- Positive: no port exposure, no shared namespace, bounded blast radius,
  reproducible teardown (`docker compose down` scoped to `agent-factory-dev`).
- Negative: the API is not reachable from the host; debugging happens through
  `docker compose exec` or a future reverse-proxy route added through Traefik
  labels in a later story (which requires a human decision, since it touches the
  shared edge).
- Capacity stays tight: a full CI runner or a second worker will need a
  decision on host sizing before sprint 3 (recorded as a risk in the sprint
  closeout).
