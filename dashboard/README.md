# Camilia — AI Agent Factory

Dashboard « command center » pour définir, construire, approuver, livrer et surveiller des agents IA.

Le flux central, avec ses portes humaines :

```
Créer un agent → Spécification → Backlog Jira → VOTRE approbation → Développement → Tests → Déploiement → Monitoring
```

Aucun développement ni déploiement ne démarre avant l'approbation explicite du backlog. Aucune promotion en
production sans Go/No-Go humain. Aucun chiffre affiché n'est simulé : si la donnée n'existe pas, l'écran affiche
« n/a » ou un état vide.

## État — Sprint 1 (socle)

Livré :

- Application React + TypeScript, build statique (Vite), servie par nginx — 180 Ko, ~10 Mo de RAM.
- Page **Vue d'ensemble** branchée sur l'API AI Factory réelle (`/root/nourtech`, 48 routes) :
  workflows et portes d'approbation, définitions de chaîne, évaluations, releases, registre d'agents,
  santé système (mémoire, disque, uptime), état Jira, journal d'audit.
- Stack Compose isolée (`camilia`), réseau dédié, limites mémoire, route Traefik par labels sur
  `camilia.nourredine.tech`, basic auth (identifiants hors dépôt).
- Proxy same-origin `/api` : le navigateur ne connaît jamais l'hôte de l'API, aucun CORS.

Non livré (sprints suivants, pages volontairement vides) : wizard de spécification, backlog Jira,
sprints, GitHub, CI/CD, monitoring détaillé, réglages.

## Développement

```bash
npm install
npm run dev            # http://127.0.0.1:5173, proxy /api vers 127.0.0.1:8081
npm run build          # tsc --noEmit && vite build → dist/
```

L'API doit tourner sur `127.0.0.1:8081` (prod AI Factory) ou être pointée par `vite.config.ts`.

## Vérification locale dans Docker (loopback seulement)

```bash
# htpasswd de test
printf 'soufiane:%s\n' "$(openssl passwd -apr1 devonly)" > /tmp/htpasswd-dev
chgrp 101 /tmp/htpasswd-dev && chmod 640 /tmp/htpasswd-dev   # gid 101 = nginx dans l'image

CAMILIA_HTPASSWD=/tmp/htpasswd-dev docker compose -f compose.yaml -f compose.dev.yaml up -d --build
python3 scripts/smoke_camilia.py http://127.0.0.1:8090 soufiane:devonly
```

## Déploiement (nécessite un accord explicite)

```bash
# 1. identifiants : jamais dans le dépôt, jamais dans l'image
printf 'soufiane:%s\n' "$(openssl passwd -apr1 'mot-de-passe')" > /opt/camilia/htpasswd
chgrp 101 /opt/camilia/htpasswd && chmod 640 /opt/camilia/htpasswd

# 2. stack
cd dashboard && docker compose up -d --build
```

Le fichier htpasswd est monté en lecture seule dans nginx (`auth_basic`). Il doit être lisible par
l'utilisateur nginx de l'image (uid/gid 101) — sinon nginx répond 500. Traefik prend la route par
labels : certificat Let's Encrypt automatique, aucune modification de sa configuration.

## Structure

```
src/lib/api.ts        types et client de l'API réelle
src/pages/Overview.tsx  vue d'ensemble (données live)
src/components/       shell, KPI, panneaux
compose.yaml          stack isolée + labels Traefik
nginx.conf            bundle statique + proxy /api + basic auth
scripts/smoke_camilia.py  vérification sans dépendance
```
