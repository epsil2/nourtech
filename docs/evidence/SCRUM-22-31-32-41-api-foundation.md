# Preuve — socle API de la Factory (Sprint 2)

Tickets : SCRUM-22 (santé + PostgreSQL isolé), SCRUM-32 (API agents/runs + persistance),
SCRUM-31 (journal d'audit et métriques), SCRUM-41 (suivi des runs et de leur statut).
Date : 2026-11-14. Commit de référence : branche `feature/sp2-api-foundation`.

## Ce qui est livré

- `services/api/app/store.py` — deux implémentations derrière une interface :
  `PgStore` (PostgreSQL, DEV/Qualité/Prod) et `MemoryStore` (`FACTORY_STORE=memory`,
  tests). Schéma créé au démarrage, **additif uniquement** (aucun `DROP`, aucun
  `ALTER` destructif) : une image plus récente peut démarrer sur une base plus ancienne.
  Tables `agents`, `runs`, `audit_events` + index `runs_status_idx`, `audit_entity_idx`.
- `services/api/app/api.py` — surface HTTP `/api/v1` : `agents` (création, liste,
  lecture), `runs` (création, liste filtrable, lecture, changement de statut),
  `audit`, `metrics`, `health`. Machine à états explicite `queued → running →
  succeeded|failed|cancelled` ; toute transition non déclarée renvoie `409`.
- `services/api/app/redaction.py` — masquage par nom de clé **et** par forme de
  valeur (jetons Jira/GitHub/GitLab/OpenAI), appliqué à chaque écriture d'audit.
- `services/api/app/main.py` — conserve le contrat `healthz` / `readyz` de SCRUM-19
  dont dépendent les trois étages du pipeline.
- `.github/workflows/deploy.yml` — l'étage **DEV** prouve désormais la persistance
  réelle : création d'un agent et d'un run via l'API, transition, relecture, puis
  `select count(*) from runs` en `psql` sur la base.
- `.github/workflows/release.yml` — l'étage **Qualité** tourne sur le commit tagué
  avant que `production` ne soit autorisée à attendre le statut du déploiement.

## Preuves d'exécution

Tests locaux :

```
55 passed in 0.28s
secret scan: ok
```

Stack DEV réelle (Postgres + Redis + API), exercée depuis le conteneur `api` :

```
agent reuse-or-create: 409 {'detail': 'agent name already exists'}
run: 201 8ef5f276-fac0-431a-8e65-e8d011307e08 queued
patch running: 200
illegal queued: 409
audit rows: 3
metrics: {'uptime_seconds': 17.1, 'runs_by_status': {'running': 1}, 'runs_total': 1,
          'audit_events_visible': 1, 'store': 'PgStore'}
```

Persistance après redémarrage de l'API (le run est relu depuis PostgreSQL) :

```
runs still running after api restart: 1
{'uptime_seconds': 6.7, 'runs_by_status': {'running': 1}, 'runs_total': 1,
 'audit_events_total': 3, 'store': 'PgStore'}
```

Contrôles d'isolation inchangés (SCRUM-20) : aucun port PostgreSQL/Redis publié,
réseau `factory-internal` en `internal: true`, API non-root, `no-new-privileges`.

## Ce que cette preuve ne couvre pas

- Aucun modèle de langage n'est appelé : la Factory orchestrée (SCRUM-23, SCRUM-28)
  arrive au Sprint 4. Rien ici ne consomme de service payant.
- La charge n'est pas testée (1 vCPU) ; le plafond mémoire de l'étage PROD reste 608 MiB.
- `docker compose up` sur un volume PostgreSQL déjà initialisé **conserve l'ancien
  mot de passe** : changer `FACTORY_DB_PASSWORD` exige `down -v` en DEV. En PROD le
  mot de passe est fixé à la première installation ; le runbook le documente.
