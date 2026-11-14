# Runbook — mise en production de la factory

Chemin nominal : **GitHub Actions**, workflow `deploy` (`.github/workflows/deploy.yml`).
Un seul chemin, celui-là. Le script `scripts/deploy_prod.sh` reste l'exécutant : le
pipeline ne fait rien qu'un opérateur ne puisse faire à la main.

## 1. Gates

| Étape | Déclencheur | Ce qui doit être vrai |
|---|---|---|
| `ci` (`.github/workflows/ci.yml`) | pull request, push sur `main` | tests verts, isolation Compose vérifiée, aucun secret commité |
| `deploy` job `dev` | `workflow_dispatch` sur `main` | image construite, stack DEV démarrée pour de vrai (Postgres + Redis + API), `/healthz` et `/readyz` répondent, stack détruite ensuite |
| `deploy` job `qualite` | après `dev` | tests, `docker compose config` sur dev **et** prod, scan de secrets |
| `deploy` job `production` | même run, après `qualite` | saisie manuelle `confirm=deploy`, environnement `production`, branche `main`, un seul déploiement à la fois (`concurrency`) |

Chaîne complète : **DEV → Qualité → Production**. Rien ne part en production sur un
simple merge : il faut lancer le workflow à la main, et le job `production` ne
démarre que si `dev` puis `qualite` sont verts.

## 2. Déployer

1. Onglet **Actions** → workflow **deploy** → **Run workflow**, branche `main`.
2. Champ `confirm` : taper exactement `deploy`.
3. Le job `production` exige l'approbation de l'environnement `production`
   (réglage GitHub : *Required reviewers* = le propriétaire du dépôt).
4. La fin du job affiche `commit <sha> / run <id> / statut success`.

## 3. Ce que fait le job `production`

1. `git archive HEAD` du commit approuvé.
2. Envoi par SSH au VPS avec la clé `DEPLOY_SSH_KEY` (secret d'environnement).
   La clé est enregistrée côté serveur avec `restrict,command="/opt/agent-factory/bin/receive.sh"` :
   elle n'exécute que `deploy <sha 40 hexadécimaux>`, tout le reste est refusé (code 2).
3. `receive.sh` extrait l'archive dans un répertoire temporaire, `rsync --delete`
   vers `/opt/agent-factory/app` en préservant `deploy/prod/.env`, puis lance
   `scripts/deploy_prod.sh` avec `FACTORY_IMAGE_TAG=<sha>`.
4. `deploy_prod.sh` : préflight RAM/disque → `pg_dumpall` dans
   `/opt/agent-factory/backups/` → build de l'image → `up -d --wait` → smoke
   `/healthz` + `/readyz` → écriture de `/opt/agent-factory/state/last-good` →
   rollback automatique sur le tag précédent si une étape échoue.
5. L'API reste sur `127.0.0.1:8081` : elle n'est joignable que depuis le VPS.

## 4. Vérifier après coup

```bash
ssh root@2.24.51.199 'cat /opt/agent-factory/state/last-good'
ssh root@2.24.51.199 'curl -s http://127.0.0.1:8081/readyz'
ssh root@2.24.51.199 'cd /opt/agent-factory/app && docker compose -p agent-factory-prod ps'
ssh root@2.24.51.199 'tail -40 /opt/agent-factory/state/receive.log'
```

Attendu : le sha du run, `database ok, cache ok`, 3 conteneurs `healthy`.

## 5. Rollback

```bash
ssh root@2.24.51.199
cd /opt/agent-factory/app
FACTORY_IMAGE_TAG=$(cat /opt/agent-factory/state/previous-good) ./scripts/deploy_prod.sh
```

`deploy_prod.sh` écrit `state/previous-good` avant chaque déploiement. Si la stack
ne répond pas au smoke test, le script fait ce rollback tout seul et sort en erreur,
ce qui fait échouer le job GitHub — le run rouge est donc la trace du rollback.

Restauration de la base si nécessaire : `psql -f /opt/agent-factory/backups/<dump>.sql`.

## 6. Panne de la clé de déploiement

Si le secret `DEPLOY_SSH_KEY` est absent ou refusé, le job échoue avant tout envoi
avec `::error::DEPLOY_SSH_KEY is not set` ou `Permission denied (publickey)` :
rien n'a été déployé. Régénérer alors la paire côté VPS :

```bash
ssh-keygen -t ed25519 -N '' -C 'github-actions-deploy@nourtech' -f /opt/agent-factory/keys/deploy_ed25519
# remplacer la ligne correspondante dans /root/.ssh/authorized_keys
# puis coller la clé privée dans Settings > Secrets > production > DEPLOY_SSH_KEY
```

## 7. Ce que le pipeline ne fait pas

- Pas de scan CVE des images ni de test de charge (1 vCPU, pas de service payant).
- Pas d'exposition publique : aucun DNS, aucun vhost Traefik pour la factory.
- Ne touche à aucun autre projet Compose du VPS (Traefik, Hermes, site, trading).
