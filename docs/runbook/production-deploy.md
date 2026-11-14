# Runbook — mise en production de la factory

Un seul chemin : **GitHub Actions** `.github/workflows/deploy.yml`, chaîne
**DEV → Qualité → Production**. Le déploiement lui-même est exécuté sur le VPS par
`factory-release-watcher.timer`, qui appelle `scripts/deploy_prod.sh` : le pipeline
ne fait rien qu'un opérateur ne puisse faire à la main.

## 1. Chaîne et gates

| Étape | Déclencheur | Ce qui doit être vrai |
|---|---|---|
| `ci` (`.github/workflows/ci.yml`) | pull request, push sur `main` | tests verts, isolation Compose vérifiée, aucun secret commité |
| `deploy` job `dev` | `workflow_dispatch` sur `main` | image construite, vraie stack DEV (Postgres + Redis + API) démarrée, `/healthz` et `/readyz` répondent, stack détruite ensuite |
| `deploy` job `qualite` | après `dev` | tests, `docker compose config` sur dev **et** prod, scan de secrets |
| `deploy` job `production` | après `qualite`, `confirm=deploy` | environnement `production` approuvé, branche `main`, un seul déploiement à la fois (`concurrency`), tag `release/<sha>` publié puis résultat vérifié |

Rien ne part en production sur un simple merge : il faut lancer le workflow à la main.

## 2. Déployer

1. Onglet **Actions** → workflow **deploy** → **Run workflow**, branche `main`.
2. Champ `confirm` : taper exactement `deploy`.
3. Approuver le job `production` si l'environnement `production` demande un reviewer.
4. Attendre : le job `production` publie `release/<sha>`, le VPS déploie sous
   ~1 minute et renvoie le statut `factory/production` sur le commit. Le job passe
   au vert seulement quand ce statut est `success`.

## 3. Ce que fait le watcher du VPS

`factory-release-watcher.timer` (systemd, toutes les 60 s) exécute
`/opt/agent-factory/bin/release_watcher.sh` :

1. liste les tags `release/<sha 40 hex>` du dépôt ;
2. ignore ceux déjà déployés (`state/deployed-tags`) ou déjà en échec (`state/failed-tags`) ;
3. refuse un sha qui n'est pas un ancêtre de `main` (`compare/main...<sha>`) ;
4. poste le statut `pending`, télécharge l'archive du sha, `rsync --delete` vers
   `/opt/agent-factory/app` en préservant `deploy/prod/.env` ;
5. lance `scripts/deploy_prod.sh` avec `FACTORY_IMAGE_TAG=<sha>` : préflight
   RAM/disque → `pg_dumpall` dans `state/../backups/` → build → `up -d --wait` →
   smoke `/healthz` + `/readyz` → `state/last-good` → rollback sur le tag
   précédent si une étape échoue ;
6. poste `success` ou `failure` — c'est ce statut qui décide de la couleur du job.

Aucun runner GitHub ne touche le VPS : ni clé SSH, ni shell, ni docker.

## 4. Vérifier après coup

```bash
cat /opt/agent-factory/state/last-good
curl -s http://127.0.0.1:8081/readyz
cd /opt/agent-factory/app && docker compose -p agent-factory-prod ps
tail -40 /opt/agent-factory/state/release-watcher.log
systemctl list-timers factory-release-watcher.timer
```

Attendu : le sha du run, `database ok, cache ok`, 3 conteneurs `healthy`.

## 5. Rollback

```bash
cd /opt/agent-factory/app
FACTORY_IMAGE_TAG=$(cat /opt/agent-factory/state/previous-good) ./scripts/deploy_prod.sh
```

`deploy_prod.sh` écrit `state/previous-good` avant chaque déploiement et fait ce
rollback tout seul si le smoke test échoue ; le job GitHub passe alors au rouge.
Restauration de la base si nécessaire : `psql -f /opt/agent-factory/backups/<dump>.sql`.

## 6. Panne du watcher

- `systemctl status factory-release-watcher.timer` : doit être `active`.
- `journalctl -u factory-release-watcher.service -n 50` : erreurs de jeton, de
  téléchargement ou de déploiement.
- Le jeton lu par le watcher est dans `/root/.hermes/secrets/github.env` (600) ;
  il doit garder `Contents: Read` et `Commit statuses: Write` (et `Metadata: Read`).
- Un sha en échec est mémorisé dans `state/failed-tags` : pour réessayer,
  `sed -i '/<sha>/d' /opt/agent-factory/state/failed-tags` puis relancer le timer.

## 7. Ce que le pipeline ne fait pas

- Pas de scan CVE des images ni de test de charge (1 vCPU, pas de service payant).
- Pas d'exposition publique : aucun DNS, aucun vhost Traefik pour la factory.
- Ne touche à aucun autre projet Compose du VPS (Traefik, Hermes, site, trading).
