# nourtech — AI Agent Factory

Implementation workspace for the **AI Agent Factory** programme tracked in Jira
project `SCRUM` (site `nourtech.atlassian.net`, board 1).

Everything here is DEV-only. Production release is gated on an explicit human
Go/No-Go; nothing in this repository deploys to production.

## What exists today

| Area | Path | State |
|---|---|---|
| Read-only VPS baseline (SCRUM-17) | `scripts/vps_audit.py`, `docs/baseline/` | implemented, tested |
| Isolated DEV stack (SCRUM-19) | `deploy/dev/docker-compose.yml` | implemented, tested |
| Factory API foundation | `services/api/` | health/readiness only (SCRUM-22 extends it) |
| CI gates | `.github/workflows/ci.yml` | tests, compose isolation check, secret scan |

## Isolation rules (non-negotiable)

- Compose project name is `agent-factory-dev`; it never shares a name with an
  existing project (`traefik`, `hermes-web`, `nourredine-site`,
  `trading-platform`, `vibe-trading`, `video2tutorial`).
- The only network is `agent-factory-dev-internal` and it is `internal: true`:
  no host port, no egress.
- PostgreSQL and Redis are reachable **only** from containers on that network.
- Every service has a healthcheck, a memory ceiling and a CPU ceiling sized from
  the measured host baseline (3.9 GiB RAM, 1 vCPU).
- No `docker compose down`, restart or prune is ever run against another project.

`tests/test_compose_policy.py` fails the build if any of the above regresses.

## Usage

```bash
make venv            # test dependencies
make test            # unit + policy + security tests
make compose-check   # docker compose config validation (nothing started)
make audit           # refresh the read-only VPS baseline
make dev-up          # start the isolated DEV stack
make dev-down        # stop only the isolated DEV stack
```

DEV stack passwords come from `deploy/dev/.env` (git-ignored). Copy
`deploy/dev/.env.example` and generate one with `openssl rand -hex 16`.

## Layout

```
docs/baseline/     measured host baseline + reproduction steps
docs/adr/          architecture decisions
deploy/dev/        isolated Compose project
services/api/      FastAPI foundation (health/readiness)
scripts/           read-only operational tooling
tests/             unit, policy and security tests
```
