# Phase 1 — Audit Camilia → AI Delivery Control Plane

**Date** : 2026-11-15 · **Périmètre** : lecture seule, aucune modification d’infrastructure,
aucune création d’issue, aucun déploiement.
**Référentiel** : commit `cd7d145` sur `main` (`epsil2/nourtech`), API prod `127.0.0.1:8081`,
dashboard `https://camilia.nourredine.tech` (image `camilia-web:sprint3`).
**Méthode** : inspection du code, des fichiers de déploiement, de l’état Docker/Traefik,
des API Jira et GitHub réelles, et des ressources de l’hôte. Chaque constat cite la preuve
(fichier, commande, réponse d’API). Les points non vérifiables sont marqués comme tels.

---

## A. Évaluation exécutive

Camilia n’est pas une maquette : c’est une application de delivery réelle, en production,
avec 66 routes, 11 tables PostgreSQL, 166 tests, une chaîne CI/CD à quatre étages et une
traçabilité d’audit. Elle couvre aujourd’hui correctement **Jira (lecture + écriture gardée)**,
**GitHub (lecture)**, **qualité déterministe**, **releases** et **monitoring hôte**.

Elle n’est en revanche **pas encore un control plane** au sens du document, pour trois raisons
bloquantes, dans cet ordre :

1. **Aucune identité vérifiée côté serveur.** Toutes les opérations sensibles (créer un agent,
   approuver une spécification, publier une release, écrire dans Jira) sont ouvertes ; l’acteur
   est un en-tête `x-actor` fourni par le client. L’audit est donc *présent mais non attribuable*.
   C’est l’écart le plus grave au regard du module H.
2. **Aucune intégration Hermes.** Le mot `hermes` n’apparaît dans aucun module du service
   (`grep -rln hermes services/api/app/*.py` → vide). Le « workflow engine » est un automate
   déterministe interne (`workflow.py`), pas l’exécution d’agents. Le module C est **absent**,
   pas partiel.
3. **Le modèle de données n’a ni Project, ni Version, ni Deployment.** Onze tables plates
   (`agents` 4 colonnes, `runs` 6 colonnes) là où le document en décrit vingt-deux reliées.
   La chaîne de traçabilité complète n’existe pas encore, même si chaque maillon isolé existe
   sous une forme dégradée.

Deux écarts de moindre gravité mais à corriger tôt : le **PAT GitHub porte `admin: true`** sur le
dépôt et **`main` n’est pas protégée** (HTTP 404 sur `/branches/main/protection`).

Ce qui est solide et doit être conservé : la séparation lecture/écriture Jira avec opt-in et
allowlist, l’idempotence par label `ai-factory`, la redaction, les secrets hors dépôt, la
porte Go/No-Go adossée à un tag humain, et le refus documenté d’un composant qui ne tient pas
dans 1 vCPU (`workflow.py`, en-tête : LangGraph écarté volontairement).

**Verdict** : base saine, environ 35 % du control plane cible réalisé, aucun blocage
architectural — mais l’ordre d’exécution proposé par le document doit être **inversé sur un
point** : l’identité (H) doit précéder l’exécution Hermes (C), sinon on automatise l’exécution
d’agents derrière un endpoint non authentifié.

---

## B. Architecture actuelle et frontières

```
Navigateur (React 18 + TS + Vite, bundle 191 518 B)
   │  HTTPS, Basic auth nginx (1 compte partagé)
   ▼
Traefik (labels Docker, LE HTTP-01, cert valide jusqu’au 2027-02-12)
   ▼
camilia-web (nginx, image camilia-web:sprint3)
   ├── /            → SPA
   └── /api/*       → proxy interne http://api:8080   (réseau Docker camilia_internal)
                          │
                          ▼
              agent-factory-prod-api-1  (FastAPI, 66 routes)
                 ├── PostgreSQL 16 (agent-factory-prod-db-1, DB factory, 11 tables)
                 ├── Redis (agent-factory-prod-cache-1)
                 ├── Jira Cloud REST v3 / Agile 1.0  (Basic auth, token hors dépôt)
                 └── GitHub REST v3                  (Bearer, GET uniquement)
Hôte : cron/timer systemd `factory-release-watcher` (1 min) → déploie les tags approuvés
CI : GitHub Actions ci / deploy / release (DEV → QA → tag humain → watcher → PROD)
```

**Frontières réelles**

- L’API n’est pas exposée : `HostConfig.PortBindings` = `127.0.0.1:8081→8080` pour l’API,
  `{}` pour la base. Conforme à « do not expose PostgreSQL or Redis publicly ».
- Un seul proxy inverse (Traefik) ; Camilia n’en installe pas un second (nginx est un serveur
  de fichiers + proxy interne au conteneur, pas un point d’entrée).
- Le dashboard ne détient aucun secret : uniquement des chemins `/api/...` relatifs.
- Le conteneur web joint l’API par le réseau Docker interne, jamais par l’hôte.

---

## C. Matrice de capacités

Statuts : **Implémenté et vérifié** (IV) · **Partiel** (P) · **Absent** (A) · **Non sûr** (NS) ·
**Non vérifiable** (NV).

| Module du document | État | Preuve / ce qui manque |
|---|---|---|
| A. Project lifecycle (demande, KPI, parties prenantes, risques) | P | `api_requests.py` + `chain.py` créent une demande et enchaînent discovery→delivery→release ; mais aucune entité Project, aucun KPI métier, aucun stakeholder, aucun registre de risques. `chain.py` documente que chaque étape s’arrête sur une porte humaine. |
| A. Agent (statut de cycle de vie, version, provider/modèle, skills, permissions, schémas E/S, mémoire, limites de coût) | P | `agents` = `id, name, description, created_at` (4 colonnes). `agent_specs` porte `status` + `approved_by/at` et un `payload` jsonb : la vie de l’agent s’arrête à la spécification. Aucun statut Draft→Deployed, aucune version, aucun provider. |
| B. Backlog proposé depuis la spec, revue humaine, approbation, création idempotente | P | `planner.py` produit un plan ; `plans` (`issue_key`, `approved_by`, `pull_request_url`) + `POST /plans/{id}/approve` ; `jira_write.py` crée avec label `ai-factory` et déduplication par JQL (l. 165). Manque : proposition Epic/Story/Task with acceptance criteria et dépendances éditable avant approbation, et synchronisation de statut retour. |
| B. Synchronisation du statut Jira dans Camilia | A | Aucune table `jira_issues` ; l’état est relu à la volée par les endpoints board/backlog. Pas de persistance de la relation. |
| C. Hermes : soumettre, suivre, annuler, récupérer logs/preuves | A | `grep -rln hermes services/api/app/*.py` → vide. `runs` est une table de suivi manuel (`PATCH /runs/{id}` change le statut). Aucun adaptateur, aucun appel. |
| D. GitHub lecture (dépôt, PR, workflows, releases) | IV | `github.py` (client GET-only, `token_present` booléen), `api_github.py` 6 endpoints, vérifié en prod : `epsil2/nourtech`, 5 PR, workflow `success`. |
| D. GitHub écriture (branche, PR, statuts) | A | Volontairement absent ; aucun endpoint POST. |
| E. Évaluations (ID, version de spec, commit, seuils, preuves) | P | `evaluations` (12 colonnes : score, threshold, passed, critical_failures, review) + `evaluation.py` déterministe (jeu `quality` + `safety`, échec critique). Manque : lien vers un commit/build immuable, jeux de tests unitaires/intégration/E2E, tests d’injection d’outils, RAG, perf. |
| F. Releases, artefacts, promotion, Go/No-Go, rollback | IV | `releases` (13 colonnes : version, commit_sha, target, status, approved_by, rolled_back_to), `artifacts`, endpoints readiness/plan/approve/publish/rollback ; Go/No-Go = tag humain `SP*-Release`/`release/<sha>` (`.github/workflows/release.yml` l. 5-12). |
| F. Déploiement effectif depuis Camilia | P | Le déploiement réel est fait par `factory-release-watcher` (timer systemd actif depuis 2026-11-14 15:52, toutes les minutes) + `scripts/deploy_prod.sh`. Camilia enregistre et expose, elle ne déclenche pas. |
| G. Monitoring hôte | IV | `system.py` lit `/proc`, `statvfs`, renvoie `None` si illisible. Prod : mémoire 60 %, disque 67 %, charge 0,76, uptime 13 j 22 h. |
| G. Coût IA, tokens, budgets | A | Aucun appel de modèle dans la plateforme, donc aucune consommation à mesurer ; aucune table `metric`/`budget`. |
| G. KPI métier | A | Absent. |
| H. Identité serveur, RBAC, sessions | NS | `_actor(x_actor)` retourne `x_actor or "anonymous"` (`api.py` l. 53-54). Aucun `Depends`, aucun `HTTPBasic`, aucun jeton d’API dans `services/api/app/*.py`. Le seul contrôle est le Basic auth nginx, partagé, et le bind localhost. |
| H. Audit | P | `audit_events` (7 colonnes) alimentée partout, payload redacté ; mais `actor` forgeable → **non attribuable**. |
| H. Secrets | IV | Hors dépôt (`/opt/agent-factory/secrets/`, 0600), `secret_scan.py` en CI, `redaction.py`, aucun secret dans le frontend ; test `test_deploy_pipeline.py::test_pipeline_carries_no_credential`. |
| Dashboard IA (Command Center / Factory / Delivery / DevOps / Operations / Administration) | P | 8 vues réelles (Overview, Agents, Backlog, Sprints, GitHub, Releases, Monitoring, Intégrations). Manquent : Activity, Approval Center, Agent Builder multi-étapes complet, Skills Library, Projects, Hermes Execution, Pull Requests dédié, AI Cost, KPIs, Audit/Governance, Users/Permissions. |

---

## D. Faisabilité d’intégration Hermes (vérifiée)

Installation réelle : `hermes` v0.23.0 (2026.9.24), install git dans `/root/.hermes/hermes-agent`,
profil actif `default`, profils `soku` et `trader`.

**Ce qui existe (mesuré, pas supposé)**

| Surface | Preuve | Verdict pour Camilia |
|---|---|---|
| API HTTP de tâches | `curl http://127.0.0.1:9119/openapi.json` → **302** ; `/login` → `<title>Sign in — Hermes Agent</title>` | **Non** : c’est l’UI web du dashboard, authentifiée par session (`hmac.compare_digest(... "Bearer " + _SESSION_TOKEN)` dans `hermes_cli/web_server.py` l. 308). Contrat de navigateur, pas d’intégration machine. |
| Gateway control socket | `gateway/control_socket.py`, verbes `identify`/`status` | Local, UNIX, authentifié par permissions FS ; pas de soumission de tâche. |
| **Kanban (CLI, SQLite)** | `hermes kanban create --idempotency-key --max-runtime --max-retries --workspace --branch --completion-contract --json`, `hermes kanban dispatch --max --failure-limit`, `hermes kanban runs <id> --json`, `hermes kanban list --json` | **Oui — contrat supporté et suffisant.** Il couvre idempotence, timeout, retries, isolation par workspace/worktree, dépendances (`--parent`), assignation à un profil, et renvoie l’historique d’exécution en JSON. |
| ACP (`acp_adapter/`) | adaptateur Agent Client Protocol présent | Possible mais surdimensionné : JSON-RPC sur stdio, processus persistant, local. À garder comme option si un jour un client tiers doit piloter Hermes. |
| Cron / webhook | `hermes cron`, `hermes webhook` disponibles | Utiles pour déclencher un cycle à heure fixe ou sur événement, pas pour un suivi par tâche. |

**Recommandation** : implémenter l’adaptateur Camilia → Hermes **sur le Kanban uniquement**
(`hermes kanban create` avec `--idempotency-key <execution_id>`, `--max-runtime`, `--max-retries`,
`--workspace worktree`, puis `dispatch`, puis `runs --json` pour l’évidence et l’état).
Aucun shell arbitraire exposé : Camilia n’exécute que des verbes kanban nommés, avec des
paramètres validés.

**Limites à documenter dans le contrat d’adaptateur**

- Kanban est **local à la machine** (SQLite `~/.hermes/kanban.db`, 118 784 B, mode 644) :
  l’adaptateur doit tourner sur le VPS, pas dans le conteneur API — ou le conteneur doit
  monter un répertoire de travail et appeler le binaire par un chemin contrôlé.
- Les tâches sont exécutées par un profil Hermes (`--assignee`), donc l’isolation entre
  projets Camilia dépend de la discipline de nommage des boards, pas d’un contrôle d’accès
  par tenant.
- **Non vérifié** : présence de tokens/coût par run dans `runs --json` (base vide, `[]`).
  À confirmer avant de promettre le module G « AI cost » sur cette source.
- Le mode 644 de `kanban.db` est lisible par tout utilisateur local ; sans impact sur un VPS
  mono-utilisateur, à resserrer en 600 dans le durcissement.

---

## E. Jira — état réel du projet

Relevé en lecture via l’API REST (`/rest/api/3/...`, `/rest/agile/1.0/...`).

- Projet `SCRUM` = « Ai Factory Software Team », id `10000`, lead *Epsilon Nour*, style *classic*.
- **Types d’issue réellement configurés : Epic, Story, Task, Subtask.** Pas de `Test`, pas de
  `Bug`. Le document interdit d’en supposer : toute tâche de test doit être une `Task`/`Story`
  étiquetée, ou l’ajout du type doit être demandé.
- **Statuts : To Do, In Progress, In Review, Done** (workflow simple).
- Board `1` = « SCRUM board », type `simple`, projet unique SCRUM.
- Backlog : **53 issues** (20 Done, 30 To Do, 3 In Progress). Champ sprint `customfield_10020`.
- Écriture existante : `jira_write.py` opt-in (`JIRA_WRITE_ENABLED`), allowlist projet,
  idempotence par label `ai-factory` + recherche JQL préalable, audit `x-actor`.

**Risques de doublons constatés (à trancher, aucune suppression faite)**

| Constat | Détail |
|---|---|
| SCRUM-42 / SCRUM-43 | Résumés identiques (registre des agents / versionnement). Créés en double. |
| SCRUM-37 / SCRUM-38 | Recouvrement fort : « implémenter les changements approuvés avec une PR liée ». |
| SCRUM-1 à SCRUM-4 | Placeholders (« Task 1 »… »Task 4 »), aucun contenu utile. |
| SCRUM-53 | Artefact du test d’écriture `smoke_prod --write` : issue de test dans le vrai backlog. |

Aucun ticket ne couvre le Sprint 2 ni le Sprint 3 du dashboard (spécifications, 7 pages,
monitoring, intégrations) : le travail livré n’est pas tracé côté Jira, alors qu’il l’est en
Git. C’est un manque de traçabilité inverse de celui qu’on attend d’un control plane.

---

## F. GitHub et CI/CD

| Élément | État mesuré |
|---|---|
| Dépôt | `epsil2/nourtech`, public, branche par défaut `main` |
| Jeton | PAT *fine-grained* (`X-OAuth-Scopes` absent) avec permissions **`admin: true`, `maintain: true`, `push: true`** → **très au-delà du moindre privilège** |
| Protection de `main` | **HTTP 404 « Branch not protected »** |
| Workflows | `ci`, `deploy`, `release` — tous `active` |
| `deploy.yml` | sur `push` `main` : construit l’image, démarre une stack DEV **jetable** sur le runner, `/healthz` + `/readyz`, preuve de persistance Postgres, puis `down -v`. Ne touche pas la prod. |
| `release.yml` | sur tag `SP*-Release` ou `release/<sha>` : qualité (166 tests, compose dev+prod, scan de secrets), vérifie que le SHA est sur `main`, attend le statut `factory/production` posé par le VPS (540 s). **La porte humaine est le tag.** |
| Watcher VPS | `factory-release-watcher.timer` **actif** (`active (running)` depuis 2026-11-14 15:52), oneshot chaque minute, `scripts/release_watcher.sh` |
| Environnement GitHub `production` | existe mais **aucune règle de protection** (0 reviewer requis) |
| Releases/tags publiés | 0 |
| Historique | 5 runs récents, tous `success` (dont `feat/camilia-dashboard-sprint3`) |

Conclusion : la chaîne à quatre étages est **réelle et vérifiable**, mais elle repose sur un
jeton administrateur et une branche non protégée. Le « Go/No-Go » est solide côté workflow,
fragile côté droits : quiconque détient le jeton peut pousser sur `main` et poser le tag.

---

## G. Identité, sécurité, gouvernance — analyse d’écart

| Contrôle attendu | Réalité | Écart |
|---|---|---|
| Identité vérifiée serveur | `x-actor` auto-déclaré, défaut `anonymous` | **Critique** |
| RBAC (Admin…Viewer) | inexistant (aucune table `users`) | **Critique** |
| Autorisation par projet | inexistante | Élevé |
| CSRF | pas de cookie de session côté API ; Basic auth rejouée par le proxy → surface limitée mais non traitée explicitement | Moyen |
| Audit non répudiable | `audit_events` en base, `actor` fourni par le client, aucune signature/chaîne de hachage | Élevé |
| Secrets | hors dépôt, 0600, scan CI, redaction | **Conforme** |
| Shell arbitraire | aucun endpoint n’exécute de commande | **Conforme** |
| Exposition réseau | API `127.0.0.1:8081`, DB/Redis non publiés | **Conforme** |
| Auth du dashboard | Basic auth nginx, **un compte partagé**, mot de passe de 6 caractères | Moyen |
| Surface hors périmètre (VPS partagé) | `nourredine.tech` expose `/.backup/*.html` et `/.htaccess` en 200 (constat antérieur, correctif non appliqué) | Élevé, **hors Camilia** |

---

## H. Modèle de données proposé (additif)

Principe : **rien à détruire**. Les 11 tables existantes restent, on ajoute les entités
manquantes et on rattache les existantes par clés étrangères nullables, en trois migrations.

**Existant → cible**

| Table actuelle | Devient | Action |
|---|---|---|
| `agents` (4 col.) | `agents` + `agent_versions` | `ALTER TABLE agents ADD status, purpose, provider, model, limits jsonb, version int` |
| `agent_specs` | `specification_versions` | garder ; ajouter `project_id`, `version`, `supersedes_id` |
| `plans` | `jira_issues` | garder `issue_key` ; extraire vers `jira_issues(project_id, key UNIQUE, type, status, synced_at)` |
| `runs` | `executions` + `execution_events` | garder `runs` ; ajouter `execution_ref`, `hermes_task_id UNIQUE`, `started_at`, `finished_at`, `exit_code`, `evidence jsonb` |
| `releases` | `releases` + `deployments` | `target` devient `environment_id` FK ; `deployments(release_id, environment_id, status, health jsonb, started_at, finished_at)` |
| `artifacts` | `artifacts` | ajouter `repository_id`, `commit_sha` indexé, `immutable_digest` |
| `evaluations` | `evaluations` + `evaluation_results` | `results jsonb` reste, ajouter `commit_sha`, `suite_version` |
| `audit_events` | `audit_events` | ajouter `actor_id` FK nullable, `prev_hash`, `hash` (chaînage) |
| — | `projects`, `project_members`, `approvals`, `repositories`, `branches`, `pull_requests`, `workflow_runs`, `environments`, `metrics`, `budgets`, `integration_connections` | création |

**Contraintes et index minimaux**

- `jira_issues.key` UNIQUE, `executions.hermes_task_id` UNIQUE (idempotence de bout en bout),
  `deployments(release_id, environment_id)` UNIQUE, `approvals(subject_type, subject_id, decision)`
  indexé, `audit_events(at DESC)`.
- Énumérations explicites en `CHECK` (statuts de cycle de vie, environnements, décisions).
- `integration_connections` ne stocke **que** le nom des variables d’environnement, jamais la valeur.

**Chaîne de traçabilité visée**

`project → agent → specification_version → approval → jira_issue → execution → branch/PR → CI+QA evidence → release → deployment → metric → improvement`

Chaque flèche devient une FK ; aujourd’hui quatre de ces liens n’existent pas du tout
(project, execution, deployment, metric).

---

## I. Contrats d’API et modèle asynchrone (propositions, non implémentées)

Règle : aucune opération longue dans la requête HTTP ; tout ce qui touche Hermes, GitHub ou
un déploiement passe par un job persistant.

```
POST /api/v1/projects/{id}/backlog-proposals     → 202 {proposal_id}     (génération)
GET  /api/v1/proposals/{id}                      → état + items éditables
POST /api/v1/proposals/{id}/approve              → 200 {approved_by, at}  (identité serveur)
POST /api/v1/proposals/{id}/materialize          → 202 {job_id}          (création Jira idempotente)
POST /api/v1/executions                          → 202 {execution_id}    (adapter Kanban)
POST /api/v1/executions/{id}/cancel              → 202 {job_id}
GET  /api/v1/executions/{id}/events?after=<seq>  → flux incrémental (polling 2 s ou SSE)
POST /api/v1/releases/{id}/deploy                → 202 {job_id, environment}
GET  /api/v1/jobs/{id}                           → {state, attempts, last_error, result}
```

Table `jobs(id, kind, subject_id, state queued|running|succeeded|failed|cancelled, attempts,
max_attempts, next_attempt_at, last_error, created_at, updated_at)` ; worker unique
(pas de file lourde : le VPS n’a pas la marge pour Redis Streams + Celery), avec reprise au
démarrage (`state='running'` orphelin → `queued`).

**Idempotence** : clé fournie par l’appelant (`Idempotency-Key`) stockée sur le job ; pour
Hermes, elle est passée telle quelle à `kanban create --idempotency-key`.

---

## J. Ressources de l’hôte et sûreté d’infrastructure

Mesuré le 2026-11-15 à 13:22 UTC.

- 1 vCPU, charge 1,12 / 1,13 / 1,08 — machine déjà occupée, pas de marge pour un build parallèle.
- RAM 3 915 Mo : 1 906 utilisés, **2 008 disponibles**, cache 2 039.
- **Swap 2 047 Mo dont 1 381 utilisés (67 %)** : signal de pression mémoire réel.
- Disque `/` 48 Go, 30 Go utilisés (**64 %**), 18 Go libres.
- 14 conteneurs actifs ; consommation observée : API 50 Mo, Postgres 24 Mo, Redis 3 Mo,
  trading-api 85 Mo, Traefik 22 Mo, le reste < 25 Mo.
- Ports en écoute sur l’hôte : 8081 (API, loopback), 9119/9120/46061 (Hermes), plus ceux de Traefik.

Conséquences pour les phases suivantes :

- Pas de nouveau service d’observabilité (Prometheus/Grafana/Langfuse interdits sans accord
  explicite) : le monitoring reste `system.py` + tables en base.
- Pas de LangGraph, pas de Celery/Redis Streams : le worker `jobs` doit être un thread/processus
  du conteneur API avec `SELECT ... FOR UPDATE SKIP LOCKED`.
- Un build d’image à la fois (`NODE_OPTIONS=--max-old-space-size=768` déjà nécessaire).

---

## K. Feuille de route priorisée

| # | Lot | Dépend de | Critères d’acceptation |
|---|---|---|---|
| 1 | **Durcissement immédiat** : auth serveur (Bearer + table `users`), `actor_id` en base, protection de `main`, PAT en lecture seule + workflow de branche, mot de passe ≥ 16 car. | — | Toute route POST/PATCH/DELETE sans jeton → 401 ; audit avec `actor_id` non nul ; `GET /branches/main/protection` → 200 ; un PAT en lecture ne peut pas pousser. |
| 2 | **Modèle de vie** (migration additive) | 1 | 3 migrations réversibles ; `projects`, `approvals`, `executions`, `deployments`, `metrics` créées ; 166 tests + nouveaux tests de migration verts ; aucune perte de données. |
| 3 | **Orchestration Jira** : proposition éditable, approbation, matérialisation idempotente, synchro de statut | 2 | Une proposition approuvée crée strictement une issue par item (rejeu = 0 doublon) ; `jira_issues` reflète le statut réel ; doublons SCRUM-42/43/37/38 tranchés. |
| 4 | **Adaptateur Hermes (Kanban)** | 1, 2 | `POST /executions` → 202 + tâche kanban ; état et logs visibles en < 5 s ; annulation effective ; deux exécutions concurrentes pour la même tâche interdites par `hermes_task_id UNIQUE`. |
| 5 | **GitHub + QA** : PR, checks, preuves liées au commit | 2, 4 | Chaque exécution affiche branche, PR, run CI, évaluation rattachés au même `commit_sha`. |
| 6 | **Release management** : promotion DEV→QA→PRE-PROD→PROD pilotée depuis Camilia | 2, 5 | Un déploiement exige une approbation nominative en base ; l’artifact est un digest immuable ; rollback testé. |
| 7 | **Monitoring, coûts, gouvernance** | 2, 4 | Métriques réelles par exécution ; coût mesuré ou « Unknown », jamais estimé sans version de tarif ; budgets et alertes ; audit chaîné. |
| 8 | Multi-workspace | 7 | Hors périmètre jusqu’à accord produit. |

---

## L. Estimations, avec hypothèses

Hypothèses : 1 développeur senior, code existant maîtrisé, pas de nouvelle dépendance lourde,
builds séquentiels sur le VPS, revue humaine incluse, pas d’imprévu Jira/GitHub majeur.

| Lot | Charge | Risque |
|---|---|---|
| 1. Durcissement | 2,5 j | faible |
| 2. Modèle de vie | 3,5 j | moyen (migrations sur base prod, sauvegarde préalable) |
| 3. Jira | 4,5 j | moyen (doublons à trancher, types d’issue absents) |
| 4. Hermes Kanban | 6 j | **élevé** (contrat local, isolation, coûts non exposés) |
| 5. GitHub + QA | 5 j | moyen (droits du jeton) |
| 6. Release | 4,5 j | moyen (rollback réel à prouver) |
| 7. Monitoring + gouvernance | 6,5 j | moyen |
| **Total 1→7** | **≈ 32,5 j** | livrable en 3 sprints de 2 semaines |

Marge de sécurité recommandée : +20 % sur les lots 3, 4 et 6 (systèmes externes).

---

## M. Stratégie de tests

| Niveau | Contenu | Outil |
|---|---|---|
| Unitaire | transitions de cycle de vie, calcul d’idempotence, redaction | `pytest` (déjà 166) |
| Contrat | adaptateur Hermes contre `kanban --json` enregistré (fixtures) + un test d’intégration réel lancé manuellement, jamais en CI | `pytest` + fixture JSON |
| Intégration | Jira : création idempotente contre un projet de test, rejeu → 0 doublon | `pytest -m integration` (opt-in) |
| Sécurité | chaque route sensible sans jeton → 401/403 ; `x-actor` ignoré ; scan de secrets ; tentative de traversée de workspace Hermes | `pytest` + `scripts/secret_scan.py` |
| Reprise | job interrompu → reprise au démarrage ; tâche kanban orpheline → marquée `failed` | test avec base temporaire |
| Bout en bout | demande → spec → approbation → issues → exécution → PR → CI → évaluation → release → déploiement (stack DEV jetable, comme `deploy.yml`) | script + CI |

Règle conservée : aucun test ne frappe un service payant en CI ; les tests d’intégration réels
sont manuels, horodatés et stockés dans `docs/evidence/`.

---

## N. Risques, décisions requises, demandes d’approbation

**Risques principaux**

1. Automatiser l’exécution Hermes avant l’authentification exposerait un moteur d’agents
   derrière un endpoint non authentifié → **ordre imposé : identité d’abord**.
2. Jeton GitHub `admin` sur un dépôt public : perte du jeton = perte du dépôt.
3. Migrations sur une base de prod avec 2 Go de RAM libre et 1,4 Go de swap déjà consommés.
4. Le Kanban Hermes est local et mono-utilisateur : l’isolation multi-projets sera
   organisationnelle, pas technique, tant que le module 8 n’est pas traité.
5. Doublons Jira existants : toute matérialisation automatique les aggravera si le nettoyage
   n’est pas tranché avant le lot 3.

**Décisions requises de votre part**

1. Confirmer l’**ordre** : durcissement (lot 1) avant toute intégration Hermes ?
2. Trancher les doublons Jira : SCRUM-42/43, SCRUM-37/38, SCRUM-1..4, SCRUM-53 (fermer, fusionner,
   conserver ? — je ne supprime rien sans accord).
3. Accepter ou refuser l’ajout d’un type d’issue `Test` dans SCRUM (sinon les tests restent des
   `Task` étiquetées).
4. Valider le principe d’un **compte par personne** sur le dashboard (fin du compte partagé).
5. Autoriser la création d’un PAT GitHub **read-only** dédié (et la révocation de l’actuel après
   bascule), plus l’activation de la protection de `main`.
6. Confirmer que le monitoring reste sans Prometheus/Grafana (contrainte de ressources).

**Demandes d’approbation explicites**

- **A1** — publier ce rapport dans Confluence (page *AI Agent Factory — Master Documentation*,
  espace AF, version 2 lue) en **ajout de section**, sans toucher au contenu existant.
- **A2** — créer la branche `docs/phase1-control-plane-audit` et y committer ce rapport
  (aucun changement de code, aucune migration, aucun déploiement).
- **A3** — autoriser le lot 1 (durcissement) comme prochain chantier.

---

## Annexe 1 — Commandes exécutées (extrait)

```bash
git rev-parse --abbrev-ref HEAD && git log --oneline -1        # main cd7d145
grep -rln hermes services/api/app/*.py                          # (vide)
grep -rn "x-actor|Depends(|HTTPBasic" services/api/app/*.py     # x_actor uniquement
docker exec agent-factory-prod-db-1 psql -U factory -d factory -tAc "\dt"   # 11 tables
curl -s http://127.0.0.1:9119/openapi.json                      # 302 (login web)
hermes kanban create --help ; hermes kanban runs --help         # contrat JSON
GET /rest/api/3/issue/createmeta/SCRUM/issuetypes               # Epic, Story, Task, Subtask
GET /repos/epsil2/nourtech/branches/main/protection             # 404
GET /repos/epsil2/nourtech                                     # permissions admin=true
systemctl status factory-release-watcher.timer                  # active (running)
free -m ; df -h / ; uptime ; docker stats --no-stream
```

## Annexe 2 — Section prête à publier dans Confluence (en attente de votre accord, A1)

> **Phase 1 — Évaluation du passage en control plane (2026-11-15)**
>
> Périmètre : audit en lecture seule du dépôt `epsil2/nourtech` (commit `cd7d145`), de la
> production `camilia.nourredine.tech`, de l’installation Hermes, du projet Jira SCRUM et du
> dépôt GitHub. Aucune modification, aucune création d’issue, aucun déploiement.
>
> Constats majeurs : (1) aucune identité vérifiée côté serveur — l’acteur est un en-tête client,
> l’audit n’est pas attribuable ; (2) aucune intégration Hermes, le moteur de workflow actuel est
> déterministe et interne ; (3) le modèle de données ne comporte ni Project, ni Version, ni
> Deployment ; (4) le PAT GitHub porte des droits d’administration et `main` n’est pas protégée ;
> (5) la chaîne CI/CD à quatre étages avec porte Go/No-Go par tag humain existe et fonctionne.
>
> Contrat d’intégration Hermes retenu comme faisable : la CLI **Kanban** (`create --idempotency-key
> --max-runtime --max-retries --workspace`, `dispatch`, `runs --json`), et non l’API web du
> dashboard, qui est une session de navigateur.
>
> Ordre d’implémentation proposé : durcissement de l’identité, puis modèle de cycle de vie,
> puis orchestration Jira, puis adaptateur Hermes, puis GitHub/QA, puis releases pilotées,
> puis monitoring et gouvernance. Effort estimé ≈ 32,5 jours pour les lots 1 à 7, hors marge.
>
> Rapport complet : `docs/evidence/phase1-control-plane-audit-2026-11-15.md` (dépôt, branche de
> documentation), avec matrice de capacités, modèle de données proposé, contrats d’API et
> décisions en attente.

## Annexe 3 — Ce que je n’ai pas vérifié

- Contenu de `runs --json` du Kanban (base vide) : présence des tokens et du coût.
- Comportement réel du watcher de release de bout en bout (aucun tag publié depuis sa mise en service).
- Comportement du Kanban en cas de tâche concurrente sur le même workspace.
- Politique de rétention de `kanban.db` et des logs Hermes.
- Impact d’un ajout de type d’issue sur le board `simple` de SCRUM.
