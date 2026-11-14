# Test evidence — SCRUM-17 + SCRUM-19 (sprint 0 foundation)

Run on host `srv2115787` on 2026-11-14 (UTC), commit under test: the commit that
carries this file. Every command below was executed; outputs are quoted verbatim.

## Unit tests

```
$ make test            # .venv/bin/python -m pytest -q
.........................                                                [100%]
25 passed in 0.18s
```

Covers: audit parsers, read-only command allow-list, credential redaction (5 token
shapes), baseline summary shape, Markdown rendering, CLI file output, compose
isolation policy (9 assertions), API health/readiness, docs disabled.

## Compose validation (nothing started)

```
$ cd deploy/dev && docker compose config --quiet
CONFIG OK
project: agent-factory-dev
ports: {'api': None, 'cache': None, 'db': None}
networks: {'factory-internal': True}
```

## Integration — isolated stack startup and readiness (SCRUM-19)

```
$ docker compose up -d --build
Container agent-factory-dev-db-1    Started
Container agent-factory-dev-cache-1 Started
Container agent-factory-dev-api-1   Started

$ docker compose ps --format '{{.Name}}|{{.State}}|{{.Health}}'
agent-factory-dev-api-1|running|healthy
agent-factory-dev-cache-1|running|healthy
agent-factory-dev-db-1|running|healthy

$ docker compose exec -T api python -c "urllib.request.urlopen('http://127.0.0.1:8080/readyz')"
200
{"status":"ready","dependencies":{"database":{"host":"db","port":5432,"ok":true},
 "cache":{"host":"cache","port":6379,"ok":true}}}
```

## Integration — persistent database volume

```
$ psql -c "CREATE TABLE factory_probe(...)"  -c "INSERT ..."
 count
-------
     1
(1 row)

$ docker compose restart db        # scoped to the factory project only
$ psql -tAc "SELECT count(*) FROM factory_probe;"
1
```

Data survived a container restart: the named volume `agent-factory-dev-db-data`
is the only storage path.

## Security scan — no exposure

```
$ ss -ltnH | grep -E ':(5432|6379)\b'
OK: 5432/6379 not listening on host

$ docker compose exec -T api python -c "socket.create_connection(('1.1.1.1',443))"
OK: egress blocked -> OSError
```

`docker compose config --format json` reports no `ports` on any service; the only
network is `agent-factory-dev-internal` with `internal: true`.

## E2E — existing services unchanged (SCRUM-17 / SCRUM-19 acceptance)

Read-only baseline captured before any change and again with the factory stack
running, then diffed:

```
nouveaux conteneurs: ['agent-factory-dev-api-1', 'agent-factory-dev-cache-1', 'agent-factory-dev-db-1']
disparus: []
image/projet changes sur existants: []
compose projects avant: ['hermes-web','nourredine-site','trading-platform','traefik','vibe-trading']
compose projects apres: ['agent-factory-dev','hermes-web','nourredine-site','trading-platform','traefik','vibe-trading']
listeners avant == listeners apres (17 sockets, aucun nouveau)
conteneurs non-factory: 10 avant == 10 apres
```

No `docker compose down`, restart or prune ran against any other project. After
evidence capture the factory stack was stopped with its own scoped
`docker compose down`; the 10 pre-existing containers stayed up throughout.

## Not run / limitations

- **CI on GitHub Actions**: green on commit `b5c335e` (run 43606029664) — pytest
  suite, compose isolation check and secret scan. The first run (43605970852, on
  `944ec4e`) failed on the secret scan because pytest had cached parametrised test
  ids containing the fake token prefixes; the cases now carry human-readable ids
  and the scan excludes test caches.
- **Security scan depth**: grep-based secret detection + compose policy
  assertions; no SAST/DAST tool, no image CVE scanner (no paid service approved).
- **Load/soak test**: not run — the host has 1 vCPU and 3.9 GiB RAM, and the
  production projects take priority. Recorded as a sprint-3 capacity risk.

## Rollback

```bash
cd deploy/dev && docker compose down          # removes factory containers + network
docker volume rm agent-factory-dev-db-data    # only if the DEV data must go too
```

No other project, image, network or volume is touched by the rollback.
