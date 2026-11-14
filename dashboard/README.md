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

## Sprint 2 — création d'agent (wizard)

La page **Agents** porte le flux central : `Create New Agent → spec générée → revue → approbation`.

- La spécification est produite par `services/api/app/specification.py` : **déterministe**, sans appel à un
  modèle, même intake = même document. Chaque compétence sélectionnée produit une story avec ses tâches,
  ses critères Given/When/Then et une porte d'approbation humaine explicite.
- `POST /api/v1/specifications/draft` ne stocke rien (aperçu) ; `POST /api/v1/specifications` enregistre
  le brouillon ; `POST /api/v1/specifications/{id}/approve` est la seule voie qui **enregistre l'agent** et
  laisse une trace d'audit (`specification.approved`, `agent.registered`).
- **Aucun ticket Jira n'est créé ici** : la génération du backlog Jira est le Sprint 3, et elle exigera
  l'approbation du backlog. Le bouton s'appelle « Approve & Create Jira Backlog » parce que c'est le geste
  attendu par l'utilisateur, mais il s'arrête à l'enregistrement de l'agent et l'écran le dit.
- Rien n'est inventé : sans cible de KPI, la spécification écrit « à définir » ; un intake contenant un
  secret (token, mot de passe, clé) est rejeté en 422.

Endpoints : `GET /api/v1/specifications/catalog`, `POST /api/v1/specifications/draft`,
`POST /api/v1/specifications`, `GET /api/v1/specifications`, `GET /api/v1/specifications/{id}`,
`POST /api/v1/specifications/{id}/approve`.

## Sprint 3 — lecture Jira, GitHub, monitoring, intégrations

Les pages ajoutées lisent l'état réel ; aucune n'écrit dans Jira, GitHub ou le dépôt.

| Page | Endpoint | Ce qu'elle montre |
|---|---|---|
| Backlog Jira | `GET /api/v1/integrations/jira/board/backlog?project=SCRUM` | tous les tickets du projet, comptés par statut et par type, filtrés côté navigateur |
| Sprints | `GET /api/v1/integrations/jira/board/sprints?board=1` | sprints du board avec leur état et le nombre de tickets rattachés ; bouton `↻ Update` qui relit Jira |
| GitHub | `GET /api/v1/integrations/github/overview` | dépôt, pull requests, commits, workflows Actions, releases |
| CI/CD & Releases | `GET /api/v1/integrations/github/actions`, `/releases`, `/api/v1/releases`, `/api/v1/evaluations` | runs réels, releases GitHub et internes, évaluations passées |
| Monitoring & KPI | `GET /api/v1/system`, `/api/v1/system/events` | mémoire, disque, charge, uptime lus dans `/proc` ; compteurs de la base ; portes humaines en vigueur |
| Intégrations | `GET /api/v1/integrations` | quels fournisseurs ont réellement leurs variables, lesquelles manquent, suggestions |
| Agents | `PATCH /api/v1/agents/{id}` | renommage et description, audités au nom de l'acteur |

Choix explicites :

- **Backlog en données plutôt qu'un iframe Jira.** Un panneau embarqué dépendrait des en-têtes
  `X-Frame-Options`/CSP d'Atlassian, de la session Atlassian du visiteur et de l'ouverture de
  `nourtech.atlassian.net` dans le navigateur — trois points de rupture. La page lit l'API avec le compte
  de service et n'expose aucun secret ; les clés de ticket renvoient vers Jira.
- **Sprints en lecture seule.** Démarrer ou clôturer un sprint reste un geste manuel dans Jira : rien ici
  ne peut modifier un sprint.
- **Le monitoring n'estime rien.** `/proc` et `statvfs` illisibles ⇒ `null` affiché « n/a ». Les
  environnements QA/PRE-PROD ne sont pas instrumentés et la page le dit au lieu d'inventer un statut.
- **Aucune clé ne traverse le navigateur.** La page Intégrations ne reçoit que des booléens et les noms
  de variables manquantes ; il n'existe volontairement pas de champ de saisie de clé.
- **Un fournisseur n'est « connecté » que si ses variables existent** dans l'environnement du service ;
  Claude, OpenAI, Mistral, Ollama, Langfuse, OTLP, Slack et Confluence apparaissent donc en « absent »
  tant qu'aucune clé n'est fournie — et la page liste ce qui manquerait pour les brancher.

Tests : `tests/test_dashboard_features.py` (12 cas, GitHub et Jira simulés par des stubs, aucun appel
réseau) — formes de réponse, 503 quand GitHub n'est pas configuré, 422 sur un SHA ou un projet Jira
invalide, absence de secret dans la réponse, renommage d'agent audité, 409 sur doublon, 404 sur agent
inconnu.

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
src/lib/specs.ts      types de la spécification + endpoints
src/components/Wizard.tsx  wizard 3 étapes + écran de revue
src/pages/Overview.tsx  vue d'ensemble (données live)
src/pages/Agents.tsx  registre des agents (renommage, description) et des spécifications
src/pages/Backlog.tsx  tickets Jira du projet SCRUM
src/pages/Sprints.tsx  sprints du board 1, bouton Update
src/pages/GitHub.tsx  dépôt, PR, commits, workflows
src/pages/Releases.tsx  CI/CD, releases GitHub et internes, évaluations
src/pages/Monitoring.tsx  ressources de l'hôte, compteurs, portes
src/pages/Integrations.tsx  fournisseurs, variables manquantes, suggestions
src/lib/integrations.ts  types et appels des pages ci-dessus
src/components/       shell, KPI, panneaux
compose.yaml          stack isolée + labels Traefik
nginx.conf            bundle statique + proxy /api + basic auth
scripts/smoke_camilia.py  vérification sans dépendance
```
