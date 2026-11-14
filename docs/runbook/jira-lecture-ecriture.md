# Jira en production — lecture et écriture

Deux modules, deux niveaux de risque :

| Module | Verbe | Garde-fou |
|---|---|---|
| `services/api/app/jira.py` | GET uniquement | le module ne sait pas muter |
| `services/api/app/jira_write.py` | POST uniquement | triple verrou (voir plus bas) |

`tests/test_jira_integration.py` vérifie toujours qu'aucun verbe mutant n'existe dans
`jira.py` : la garantie « le client de lecture ne peut rien casser » reste vraie, et
les écritures sont concentrées dans un seul fichier relisible.

## Les trois verrous

1. **Opt-in explicite** — `JIRA_WRITE_ENABLED` doit valoir `true`. Absent ou `false`,
   toute écriture renvoie **403** `jira writes are disabled: set JIRA_WRITE_ENABLED=true…`.
   Déployer le code n'ouvre donc rien tant que personne ne l'active.
2. **Liste blanche de projet** — `JIRA_WRITE_PROJECT` (`SCRUM` par défaut, liste séparée
   par virgules). Le projet est vérifié sur le payload **et** sur le préfixe de la clé
   (`OPS-3` → 403), donc une clé glissée dans un appel ne peut pas viser un autre projet.
3. **Traçabilité** — chaque écriture réussie est inscrite dans `audit_events` avec
   l'auteur de `x-actor` (`jira.issue.created`, `jira.issue.commented`,
   `jira.issue.transitioned`).

Création **idempotente** : les issues portent le label `ai-factory` ; si un titre
identique existe déjà sur ce label, l'API renvoie l'issue existante avec
`"created": false` au lieu de dupliquer le backlog à chaque rejeu.

## Routes

```bash
BASE=http://127.0.0.1:8081     # loopback du VPS, via tunnel SSH

# état : configured, write_enabled, write_projects, read_only
curl -s $BASE/api/v1/integrations/jira/status

# lecture
curl -s -G $BASE/api/v1/integrations/jira/issues \
     --data-urlencode 'jql=project = SCRUM AND sprint in openSprints() ORDER BY rank' --data 'limit=20'

# création (idempotente)
curl -s -X POST $BASE/api/v1/integrations/jira/issues \
  -H 'content-type: application/json' -H 'x-actor: soufiane' \
  -d '{"summary":"Publier un radar IA hebdomadaire","issue_type":"Story",
       "description":"10 signaux sourcés, 800 mots, publication lundi 8h"}'

# commentaire (ADF construit côté serveur)
curl -s -X POST $BASE/api/v1/integrations/jira/issues/SCRUM-52/comment \
  -H 'content-type: application/json' -H 'x-actor: soufiane' \
  -d '{"text":"PRD validé — voir /api/v1/prd/<id>/markdown"}'

# transitions disponibles, puis transition par nom, id ou statut cible
curl -s $BASE/api/v1/integrations/jira/issues/SCRUM-52/transitions
curl -s -X POST $BASE/api/v1/integrations/jira/issues/SCRUM-52/transition \
  -H 'content-type: application/json' -H 'x-actor: soufiane' \
  -d '{"target":"In Progress"}'
```

Codes de retour des écritures : `201` créé, `200` transition, `403` verrou ou projet
hors liste, `422` cible inconnue (la liste des transitions valides est renvoyée),
`502` refus Jira, `503` identifiants absents.

## Où vivent les secrets

`deploy/prod/.env` ne contient plus que la base (`FACTORY_DB_*`, `FACTORY_IMAGE_TAG`).
Les identifiants Jira sont dans **`/opt/agent-factory/secrets/jira.env`** (0600, hors
dépôt git) : `tests/test_deploy_pipeline.py` scanne l'arbre du dépôt et exige qu'il ne
contienne aucun jeton, donc un `.env` avec un vrai token fait échouer la CI.

`scripts/deploy_prod.sh` ajoute automatiquement ce fichier comme second `--env-file`
s'il existe (`FACTORY_SECRETS_FILE` pour le déplacer). Un `docker compose … up -d`
lancé à la main sans ce second `--env-file` relancerait l'API **sans** Jira :
toujours passer par le script, ou ajouter l'option à la main.

## Ce qui reste volontairement fermé

- Rien n'écrit dans Jira sans `x-actor` ni audit.
- Aucune suppression d'issue n'est implémentée : on crée, on commente, on transitionne.
- Le token n'est jamais renvoyé par l'API ni journalisé (test dédié).
