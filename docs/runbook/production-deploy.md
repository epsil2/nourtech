# Runbook — mise en production de la factory

Un seul chemin : **GitHub Actions**, chaîne **DEV → Qualité → Production**, avec un
déploiement exécuté sur le VPS par `factory-release-watcher.timer`. Le pipeline
n'utilise que `scripts/deploy_prod.sh`, comme un opérateur.

## 1. Chaîne et gates

| Étape | Déclencheur | Ce qui doit être vrai |
|---|---|---|
| `ci` | pull request, push sur `main` | tests verts, isolation Compose, aucun secret commité |
| `deploy` job `dev` | **chaque merge sur `main`** | image construite, vraie stack DEV (Postgres + Redis + API) démarrée, `/healthz` et `/readyz` répondent, stack détruite ensuite |
| `deploy` job `qualite` | après `dev` | tests, `docker compose config` dev **et** prod, scan de secrets |
| `release` job `production` | **tag `release/<sha>` publié par un humain** | le sha est un ancêtre de `main`, un seul déploiement à la fois, résultat vérifié par statut de commit |

Deux moitiés volontairement séparées : un merge sur `main` prouve que le code
tourne (DEV + qualité), et **rien ne part en production sans un tag** — c'est le
Go/No-Go humain, il est daté, signé et visible dans l'historique du dépôt.

## 2. Déployer

1. Récupérer le sha de `main` à livrer : `git ls-remote origin refs/heads/main`.
2. Publier le tag de release, au choix :
   - GitHub → **Releases** → *Draft a new release* → tag `release/<sha>` → *Publish* ;
   - ou en ligne de commande : `git tag release/<sha> <sha> && git push origin release/<sha>`.
3. Le workflow `release` démarre, vérifie que le sha est sur `main`, puis attend.
4. Le watcher du VPS (≤ 60 s) déploie et renvoie `factory/production` ; le job
   passe au vert seulement sur `success`. En cas d'échec il est rouge et le
   rollback a déjà eu lieu.

## 3. Ce que fait le watcher du VPS

`factory-release-watcher.timer` (systemd, 60 s) exécute `/opt/agent-factory/bin/release_watcher.sh` :

1. liste les tags `release/<sha 40 hex>` ;
2. ignore ceux déjà déployés (`state/deployed-tags`) ou en échec (`state/failed-tags`) ;
3. refuse un sha qui n'est pas un ancêtre de `main` (`compare/main...<sha>`) ;
4. poste `pending`, télécharge l'archive du sha, `rsync --delete` vers
   `/opt/agent-factory/app` en préservant `deploy/prod/.env` ;
5. lance `deploy_prod.sh` avec `FACTORY_IMAGE_TAG=<sha>` : préflight RAM/disque →
   `pg_dumpall` → build → `up -d --wait` → smoke `/healthz` + `/readyz` →
   `state/last-good` → rollback sur le tag précédent si une étape échoue ;
6. poste `success` ou `failure`, qui décide de la couleur du job.

Aucun runner GitHub ne touche le VPS : ni clé SSH, ni shell, ni docker.

## 4. Vérifier après coup

```bash
cat /opt/agent-factory/state/last-good
curl -s http://127.0.0.1:8081/readyz
cd /opt/agent-factory/app && docker compose -p agent-factory-prod ps
tail -40 /opt/agent-factory/state/release-watcher.log
systemctl list-timers factory-release-watcher.timer
```

Attendu : le sha du tag, `database ok, cache ok`, 3 conteneurs `healthy`.

## 5. Rollback

```bash
cd /opt/agent-factory/app
FACTORY_IMAGE_TAG=$(cat /opt/agent-factory/state/previous-good) ./scripts/deploy_prod.sh
```

`deploy_prod.sh` écrit `state/previous-good` avant chaque déploiement et fait ce
rollback tout seul si le smoke test échoue. Base : `psql -f /opt/agent-factory/backups/<dump>.sql`.

## 6. Panne du watcher

- `systemctl status factory-release-watcher.timer` : doit être `active`.
- `journalctl -u factory-release-watcher.service -n 50` : jeton, téléchargement, déploiement.
- Jeton lu dans `/root/.hermes/secrets/github.env` (600) : `Contents: Read` +
  `Commit statuses: Write` + `Metadata: Read`.
- Un sha en échec est mémorisé : `sed -i '/<sha>/d' /opt/agent-factory/state/failed-tags`
  puis relancer le timer pour réessayer.

## 7. Ce que le pipeline ne fait pas

- Pas de scan CVE des images ni de test de charge (1 vCPU, pas de service payant).
- Pas d'exposition publique : aucun DNS, aucun vhost Traefik pour la factory.
- Ne touche à aucun autre projet Compose du VPS (Traefik, Hermes, site, trading).
