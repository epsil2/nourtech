# Utilisation de l'API AI Factory — parcours de bout en bout

Vérifié le 2026-11-14 sur un serveur local (`FACTORY_STORE=memory`, port 8098) : 43 routes,
116 tests verts, chaîne complète découverte → livraison → release exécutée sans erreur.

## Démarrage

```bash
cd /root/nourtech
.venv/bin/python -m pytest -q                      # 116 tests

# local, sans base de données
FACTORY_STORE=memory .venv/bin/python -m uvicorn app.main:app --app-dir services/api \
  --host 127.0.0.1 --port 8098

# avec PostgreSQL (dev/prod)
docker compose -f deploy/dev/docker-compose.yml up -d
```

`GET /healthz` = process vivant. `GET /readyz` = dépendances prêtes : **503 attendu en mode
`memory`** (pas de base à contrôler), 200 en mode PostgreSQL.

Toute décision humaine passe par l'en-tête `x-actor: <qui>` ; sans lui l'auteur est `system`.

## 1. Découverte : demande métier → PRD → revue d'architecture

```bash
POST /api/v1/workflows   {"definition":"discovery",
                          "input":{"request":"Exporter les runs du dashboard en CSV",
                                   "source_key":"SCRUM-30"}}
# -> 201 {"status":"awaiting_approval","current_step":"architecture_review", state.prd = PRD complet}
POST /api/v1/workflows/{id}/decision   {"decision":"approved","note":"architecture validée"}
# -> 200 {"status":"completed"}
```

Le PRD est produit de façon déterministe (zéro jeton LLM) et exposé en markdown via
`GET /api/v1/prd/{id}/markdown` ; il est aussi utilisable seul : `POST /api/v1/prd` puis
`POST /api/v1/prd/{id}/approve`.

## 2. Livraison : backlog → plan → build → QA → go/no-go

```bash
POST /api/v1/workflows  {"definition":"delivery",
                         "input":{"backlog":{"items":[{"index":1,"acceptance":"GET /runs.csv renvoie un CSV"}]}}}
# -> 201 {"current_step":"implement"}      (attente du dev humain)
POST /api/v1/workflows/{id}/decision {"decision":"approved"}   # -> current_step = release_ready

POST /api/v1/evaluations {"subject":"<workflow_id>","dataset":"quality",
                          "output":"## Livraison\nAcceptance criteria: signé\n","threshold":0.8}
POST /api/v1/evaluations/{eval_id}/review {"outcome":"accepted"}     # 'accepted' | 'rejected'
POST /api/v1/workflows/{id}/decision {"decision":"approved"}   # -> completed
```

Le gate `release_ready` refuse d'avancer sans une évaluation rattachée au **même workflow_id** :
c'est le lien preuve → décision. Datasets disponibles : `GET /api/v1/evaluations/datasets`.

## 3. Plan de dev rattaché à une story et à sa PR

```bash
POST /api/v1/plans   {"issue_key":"SCRUM-30","summary":"Export CSV des runs","description":"..."}
POST /api/v1/plans/{id}/approve
POST /api/v1/plans/{id}/link  {"pull_request_url":"https://github.com/epsil2/nourtech/pull/13"}
```

## 4. Chaîne de release (preuve → plan → publication)

```bash
POST /api/v1/artifacts {"version":"0.6.0","commit_sha":"<40 hex>",
                        "tests":{"passed":116,"failed":0},
                        "stories":[{"key":"SCRUM-30","summary":"Flux dashboard","status":"Done","type":"Story"}],
                        "workflow":"ci","source":"github","image":"agent-factory-api:abc1234"}
GET  /api/v1/artifacts/{commit_sha}/verify          # -> {"verified": true}
POST /api/v1/releases/readiness {"commit_sha":"<40 hex>","target":"dev","stories":[...],"blockers":[]}
POST /api/v1/releases/plan      {"commit_sha":"<40 hex>","target":"dev","stories":[...]}   # 409 si !ready
POST /api/v1/releases/{id}/approve                        # x-actor obligatoire en pratique
POST /api/v1/releases/{id}/notes
POST /api/v1/releases/{id}/publish                        # -> published
POST /api/v1/releases/{id}/rollback {"target_release_id":..., "reason":..., "schema_compatible":true}
```

Règles imposées par le code (tests associés) :

- `workflow` doit valoir **`ci`** : seuls les artefacts produits par le workflow GitHub Actions
  approuvé sont promouvables ; `source` doit valoir `github` (un miroir GitLab ne déclenche
  jamais de déploiement).
- `commit_sha` = 40 caractères hexadécimaux, `version` = `X.Y.Z`, clés de stories `SCRUM-<n>`.
- `readiness` exige une évaluation **dont le sujet est le commit** ; sans elle :
  `{"ready": false, "missing": ["evidence"]}` et `plan` répond 409.

## 5. Supervision

```bash
GET /api/v1/runs?limit=20                 # runs (workflows) et statuts
GET /api/v1/runs/{run_id}/timeline        # chronologie d'un run
GET /api/v1/observability/audit?limit=50  # qui a décidé quoi
GET /api/v1/observability/resources       # CPU, RAM, disque, compteurs
GET /api/v1/metrics                       # taux de succès, latence
```

## 6. Jira (lecture seule)

```bash
GET /api/v1/integrations/jira/status    # {"configured": false} tant que les secrets ne sont pas montés
GET /api/v1/integrations/jira/issues    # stories/tâches du projet, filtrables
GET /api/v1/integrations/jira/schema    # types et transitions disponibles
```

## Écarts connus au 2026-11-14

- L'instance `agent-factory-prod` (image `e802dce`, 127.0.0.1:8081) est **antérieure** aux modules
  release et observabilité : `/api/v1/releases*`, `/api/v1/artifacts*`, `/api/v1/observability/*`
  y répondent 404. Redéploiement nécessaire pour servir les 43 routes.
- Aucune exposition publique (bind 127.0.0.1) : accès par SSH ou tunnel.
