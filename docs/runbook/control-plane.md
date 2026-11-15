# Control plane — projets, exécutions, déploiements

Périmètre livré : modules A (projets/agents), B (backlog Jira), C (exécutions Hermes),
F (releases/déploiements) et les métriques de G, **hors gouvernance et sécurité**
(Module G exclu à la demande).

## Séparation des rôles

| Qui | Fait quoi |
| --- | --- |
| Camilia | enregistre, propose, gate, trace. Ne lance jamais un agent lui-même. |
| Hermes (CLI kanban) | exécute une tâche, tient l'état réel du run. |
| Jira | source de vérité des tickets. |
| Pipeline + watcher | publient en prod ; Camilia enregistre la demande et la preuve. |

## Flux

1. `POST /api/v1/projects` — la demande business crée un projet (`key`, `name`,
   `business_request`, `objectives`, `kpis`, `jira_project`).
2. `POST /api/v1/projects/{id}/proposals` — reprend la dernière spécification
   **approuvée** de l'agent et propose Epic / Stories / Sous-tâches. Rien n'est créé
   dans Jira. Refus 409 s'il n'existe aucune spec approuvée.
3. `PATCH /api/v1/proposals/{id}` — édition des items tant que le statut est `draft`.
4. `POST /api/v1/proposals/{id}/approve` — `{"actor": "..."}` obligatoire ; l'acteur
   `anonymous` est refusé. L'approbation est auditée.
5. `POST /api/v1/proposals/{id}/materialize` — crée les tickets en tâche de fond
   (202 + `poll`). Idempotent : un second passage retrouve les tickets existants et
   n'en crée aucun. Refus 409 si `JIRA_WRITE_ENABLED` n'est pas actif.
6. `POST /api/v1/projects/{id}/jira/sync` — relit les statuts Jira.
7. `POST /api/v1/executions` — crée une tâche kanban Hermes (`idempotency-key`
   recommandée, `max_runtime`, `max_retries`, `dispatch`). Refus 409 si le binaire
   Hermes est absent : aucune exécution n'est inventée.
8. `POST /api/v1/executions/{id}/sync` — relit `hermes kanban show` et convertit
   l'état réel. `cancel` bloque la tâche côté Hermes et garde le statut `cancelled`.
9. `POST /api/v1/deployments` puis `/confirm` — PRE-PROD et PROD exigent un opérateur
   nommé et une preuve ; sans preuve le déploiement reste `awaiting_approval`.
10. `GET /api/v1/cp/metrics` — taux de réussite, durée moyenne, tickets, déploiements.
    Le coût reste `n/a` (aucune donnée de coût par run côté CLI).

## Persistance

Table unique `cp_documents` (`kind` : project, proposal, approval, execution,
execution_event, deployment, metric_snapshot, job, environment), créée au démarrage
de `PgStore`. Pas de clé étrangère inter-entités : l'unicité (tâche Hermes, clé Jira,
déploiement par release/environnement) est tenue par la couche service et couverte
par `tests/test_control_plane.py`.

## Jobs

`cp_jobs` : un thread borné par `CP_MAX_JOBS` (défaut 2, max 8), état persisté avant
démarrage, `GET /api/v1/jobs/{id}` pour le polling. `CP_JOBS_SYNC=1` exécute le job
en ligne (tests). Les jobs `running` orphelins sont marqués `failed` au démarrage.

## Variables

- `HERMES_BIN` — chemin du binaire Hermes (défaut `hermes`, résolu par `PATH`).
- `HERMES_KANBAN_TIMEOUT` — délai par appel CLI (défaut 45 s).
- `CP_MAX_JOBS` — concurrence des jobs (défaut 2, plafond 8).
- `CP_JOBS_SYNC` — `1` pour exécuter les jobs en ligne.

## Ce qui reste absent

- **Module G — gouvernance et sécurité** : pas d'identité serveur (l'acteur reste
  l'en-tête `x-actor`), pas de rôles, pas de politique d'approbation centralisée.
- **Hermes non joignable depuis le conteneur API en prod** : `hermes` n'est pas dans
  l'image. La page Exécutions l'affiche et refuse de créer une exécution ; monter le
  binaire (et l'état kanban) dans le conteneur est une décision d'infrastructure
  distincte.
- Pas d'appel LLM (aucune clé sur le serveur) ; pas de coût par run.
