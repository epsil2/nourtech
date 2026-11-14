# SCRUM-21 — delivery pipeline and production deployment evidence

Date: 2026-11-14 (UTC) · Commit deployed: `eab8f48f51e83470438718f1f7dc75d797afab70`
Branch: `feature/scrum-21-gitlab-production-pipeline` · PR #2

## What was built

| piece | file | state |
|---|---|---|
| GitLab pipeline (qualite -> production) | `.gitlab-ci.yml` | written, validated |
| One-way GitHub -> GitLab mirror | `.github/workflows/mirror-to-gitlab.yml` | written, needs `GITLAB_TOKEN` + `GITLAB_PATH` |
| Production stack | `deploy/prod/docker-compose.yml` | deployed and healthy |
| Deploy / rollback script | `scripts/deploy_prod.sh` | executed twice on production |
| Runbook | `docs/runbook/production-deploy.md` | — |
| Decision record | `docs/adr/0002-gitlab-delivery-pipeline.md` | — |

## Quality gates

- `pytest`: **44 passed** (25 before this change). New suites: `tests/test_prod_policy.py`
  (13 assertions) and `tests/test_gitlab_pipeline.py` (7 assertions).
- GitHub Actions: run **43611324742** on `eab8f48` — green (pytest, compose isolation,
  secret scan).
- Compose: `deploy/prod` resolves with project `agent-factory-prod`, api published on
  `127.0.0.1:8081` only, db/cache on an internal network.
- Secret scan: no credential shape in the tree; the pipeline file only carries the
  patterns used by the scan.

## Production deployment (executed)

```
[deploy] preflight: 1500 MiB RAM available, 17 GiB free on /
[deploy] previous known-good tag: 532cd72d9131bfd580c53778f6e668f80c5f7220
[deploy] database backup: /opt/agent-factory/backups/prod-20261114T154054Z.sql.gz
[deploy] deployed eab8f48f51e83470438718f1f7dc75d797afab70: /healthz and /readyz are green
```

| check | result |
|---|---|
| `GET /healthz` | `{"status":"ok","env":"prod"}` |
| `GET /readyz` | `database ok, cache ok` |
| `ss -ltn` | `127.0.0.1:8081` only — no `5432`/`6379` on the host |
| containers | 3 healthy |
| memory used | api 33.8 / db 47.8 / cache 7.6 MiB against 608 MiB of ceilings |
| `MemAvailable` after | 1500 MiB |
| pre-existing production containers | 10 untouched (uptimes 11–12 days, same 5 Compose projects) |
| `state/last-good` | `eab8f48…` |
| backups | `/opt/agent-factory/backups/prod-*.sql.gz` (5 kept) |

## Defects found by deploying for real

1. **Unreachable endpoint** — Docker creates no loopback DNAT for a port published on
   an `internal: true` network: `127.0.0.1:8081` answered nothing even though the
   container was healthy. Fix: db and cache stay on the internal network, the api also
   joins a routable edge network; a test pins that the api is its only member.
2. **Rollback that never ran** — the cleanup hung off `trap ... ERR`, and an explicit
   `exit` in `fail()` does not fire it, so a failed smoke test left the broken stack
   running. Fix: `trap ... EXIT` plus a `STACK_TOUCHED` flag so a preflight failure
   cannot stop a healthy stack. Verified: a placeholder password exits 1 and leaves the
   three containers up.

## Not done

- No GitLab project or credential exists: the GitLab pipeline has never run, and the
  mirror workflow has never executed. This is the remaining gap for SCRUM-21.
- Rollback to a previous tag has a usable tag available but has not been rehearsed on
  production (it would interrupt a healthy service).
- No image CVE scan, no load test (1 vCPU, no paid service approved).
