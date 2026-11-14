# SCRUM-21 — CI/CD de release vers la production (GitHub Actions)

Date : 2026-11-14 · Branche : `feature/scrum-21-gitlab-production-pipeline` · VPS : `srv2115787`

## Livré

- `.github/workflows/deploy.yml` — job `qualite` (tests, `docker compose config`
  dev + prod, scan de secrets) puis job `production` :
  `workflow_dispatch` avec `confirm=deploy`, `needs: qualite`, environnement
  `production`, `main` uniquement, `concurrency: production-deploy`.
- `.github/workflows/ci.yml` — gates sur PR et sur `main`.
- `/opt/agent-factory/bin/receive.sh` — commande forcée de la clé de déploiement :
  n'accepte que `deploy <sha 40 hex>`, extrait l'archive, `rsync --delete`
  (en préservant `deploy/prod/.env`), lance `scripts/deploy_prod.sh`.
- `/root/.ssh/authorized_keys` — clé `github-actions-deploy@nourtech` avec
  `restrict,command="/opt/agent-factory/bin/receive.sh"`.
- `deploy/prod/known_hosts` — clé d'hôte du VPS épinglée (publique).
- `tests/test_deploy_pipeline.py` — 8 tests de politique.
- `.gitlab-ci.yml`, `.github/workflows/mirror-to-gitlab.yml` et
  `tests/test_gitlab_pipeline.py` supprimés : pas d'accès GitLab, chemin abandonné
  par décision du propriétaire.

## Preuves exécutées

| Contrôle | Résultat |
|---|---|
| `pytest -q` | **46 passed** (dont 8 sur le pipeline) |
| `bash -n receive.sh` | OK |
| Commande arbitraire via la clé (`id`, `rm -rf`) | **refusée**, code 2, message `expected 'deploy <40-hex sha>'` |
| Déploiement réel par le chemin du pipeline (`git archive HEAD \| ssh "deploy <sha>"`) | **OK** — `8795baad9e76951ebf377cd70e9225c3f9e049bf` |
| `/healthz` après ce déploiement | `{"status":"ok","env":"prod"}` |
| `/readyz` après ce déploiement | `database ok, cache ok` (JSON `ready`) |
| `state/last-good` | `8795baad9e76951ebf377cd70e9225c3f9e049bf` |
| Conteneurs | 3/3 `healthy` (`api` sur `127.0.0.1:8081` uniquement) |
| Conteneurs de production existants | 10, inchangés |

## Limite connue

Le secret d'environnement `DEPLOY_SSH_KEY` doit être créé par le propriétaire :
le PAT GitHub utilisé n'a pas la permission *Secrets* (`GET /repos/.../actions/secrets`
→ 403), ni *Administration* pour enregistrer un runner, ni *Environments* pour
poser les *required reviewers* via l'API. Tant que le secret est absent, le job
`production` s'arrête avant tout envoi (`::error::DEPLOY_SSH_KEY is not set`) :
aucun déploiement partiel possible.
