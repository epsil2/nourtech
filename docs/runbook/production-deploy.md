# Production deployment runbook — AI Agent Factory

Scope: the `agent-factory-prod` Compose stack (API + PostgreSQL + Redis) on the
VPS `srv2115787`. Nothing here touches Traefik, Hermes, the site, or the trading
stacks: every command is scoped with `-f deploy/prod/docker-compose.yml`.

## Mental model

```
GitHub (review, CI)  --one-way mirror-->  GitLab (pipeline)  --ssh-->  VPS
                                          qualite -> production
                                          (manual gate)      /opt/agent-factory/app
```

- **GitHub** stays the source of truth: review, pull request, `ci.yml`.
- **GitLab** runs the delivery pipeline: stage `qualite` (tests, compose, secrets)
  then stage `production`, which is **manual** and **default branch only**.
- The VPS holds a working copy at `/opt/agent-factory/app`. The production job
  rsyncs the commit there and runs `scripts/deploy_prod.sh` with
  `FACTORY_IMAGE_TAG=$CI_COMMIT_SHA`.

## Exposure

The API listens on `127.0.0.1:8081` only. Nothing is published to the internet.
Fronting it with Traefik later is a separate decision (needs a DNS record and a
router label); the API currently exposes only `/healthz` and `/readyz`.

## Resource budget

| service | memory | cpu |
|---|---|---|
| db | 320 MiB | 0.50 |
| cache | 96 MiB | 0.25 |
| api | 192 MiB | 0.50 |
| **total** | **608 MiB** | — |

The deploy aborts if `MemAvailable` < 700 MiB or free disk < 3 GiB, so it cannot
squeeze the services already running on the host.

## First-time setup (once, on the VPS)

```bash
install -d -m 750 /opt/agent-factory/app /opt/agent-factory/state /opt/agent-factory/backups
cp /opt/agent-factory/app/deploy/prod/.env.example /opt/agent-factory/app/deploy/prod/.env
sed -i "s/^FACTORY_DB_PASSWORD=.*/FACTORY_DB_PASSWORD=$(openssl rand -hex 24)/" /opt/agent-factory/app/deploy/prod/.env
chmod 600 /opt/agent-factory/app/deploy/prod/.env
```

## Deploy

Local (operator):

```bash
cd /path/to/repo
FACTORY_IMAGE_TAG="$(git rev-parse HEAD)" ./scripts/deploy_prod.sh
```

Through GitLab: **CI/CD > Pipelines > Run pipeline** on the default branch, wait
for `qualite` to pass, then press ▶ on `production:deploy`.

What the script does, in order: input checks → capacity preflight → compose
validation → `pg_dumpall` backup (last 5 kept in `/opt/agent-factory/backups`) →
`docker build -t agent-factory-api:<sha>` → `compose up -d --wait` → smoke test
`/readyz` then `/healthz` → records `<sha>` in `/opt/agent-factory/state/last-good`.
`FACTORY_DEPLOY_DRY_RUN=1` stops after the preflight.

## Rollback

Automatic: if the build, start or smoke test fails, the script redeploys the tag
in `state/last-good`; with no usable previous tag it stops the stack. The job
exits non-zero either way, so the pipeline stays red.

Manual:

```bash
cd /opt/agent-factory/app
FACTORY_IMAGE_TAG="$(cat /opt/agent-factory/state/last-good)" \
  docker compose -f deploy/prod/docker-compose.yml --env-file deploy/prod/.env up -d --wait
```

Stop without touching data: `docker compose -f deploy/prod/docker-compose.yml down`.
Delete data (irreversible): add `-v`.

## GitLab CI/CD variables to create

| variable | type | notes |
|---|---|---|
| `DEPLOY_HOST` | variable | VPS address; protected |
| `DEPLOY_USER` | variable | account allowed to run docker compose; protected |
| `DEPLOY_SSH_KEY` | **File** | private key of that account; protected, masked |

On the GitHub side (for the one-way mirror): secret `GITLAB_TOKEN`
(project access token, `write_repository`) and variable `GITLAB_PATH`
(e.g. `epsil2/nourtech`).

## Verification after a deploy

```bash
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8081/readyz
ss -ltnH | grep 8081          # 127.0.0.1:8081 only, never 0.0.0.0
cd /opt/agent-factory/app && docker compose -f deploy/prod/docker-compose.yml ps
```
