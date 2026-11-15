# Project Factory — exploitation (Phase 1)

Créer un projet logiciel depuis Camilia et orchestrer son provisioning : Jira, dépôt
GitHub, template, CI/CD, workspace Hermes, puis une tâche de développement bornée.

Camilia est le **plan de contrôle** (état, approbations, preuves) ; Hermes est le
**moteur d'exécution**. Camilia n'exécute jamais de code à la place de Hermes et ne
présente jamais une capacité planifiée comme disponible.

## Ce que fait la chaîne

| Étape | Effet externe | Idempotence |
|---|---|---|
| `create_jira` | projet Jira (clé du projet Camilia) | lookup GET `/project/{key}` avant création ; un projet existant est **adopté** |
| `create_repository` | dépôt privé sous `CAMILIA_GITHUB_OWNER` | lookup GET `/repos/{owner}/{name}` avant création |
| `apply_template` | arborescence + fichiers du template sur `main` | un commit unique, rejouable : même arbre → même commit |
| `configure_cicd` | `.github/workflows/ci.yml` | fichier versionné, aucun secret de production |
| `register_workspace` | board Kanban Hermes `proj-<clé>` | `boards create` idempotent par nom |
| `validate` | lecture Jira + GitHub + `/health` du template | aucune écriture |

États : `DRAFT → SPECIFICATION_READY → AWAITING_APPROVAL → APPROVED → CREATING_JIRA →
JIRA_CREATED → CREATING_GITHUB → GITHUB_CREATED → APPLYING_TEMPLATE → CONFIGURING_CICD →
REGISTERING_HERMES_WORKSPACE → VALIDATING → READY → DEVELOPMENT_RUNNING →
DEVELOPMENT_COMPLETED`, plus `FAILED`, `BLOCKED`, `CANCELLED`, `REQUIRES_INTERVENTION`.

## Règles non négociables

- **Aucune ressource externe n'est créée sans approbation humaine explicite.** Le bouton
  « Approuver et provisionner » enregistre une `Approval` nominative ; `POST /provision`
  répond 409 sans elle.
- **Aucune suppression automatique.** Si Jira réussit et GitHub échoue, le projet Jira
  reste, l'échec est enregistré sur l'opération, et « Relancer » reprend **à l'étape en
  échec** sans recréer Jira.
- **Une étape = une opération enregistrée** (clé d'idempotence, tentatives, dates,
  identifiant externe, erreur). L'historique complet est visible dans la page projet.
- **Les secrets restent côté serveur** : `GITHUB_TOKEN`, `JIRA_API_TOKEN` vivent dans
  `/opt/agent-factory/secrets/*.env` et ne sont jamais renvoyés au navigateur ni écrits
  dans un dépôt généré (`.env.example` vide uniquement).
- **Aucun déploiement en production** n'est déclenché par la chaîne : le premier
  environnement cible est un test non-production, la production reste soumise à un
  Go/No-Go humain.

## Configuration (hôte)

```bash
# /opt/agent-factory/secrets/jira.env     (600) JIRA_SITE_URL / JIRA_ACCOUNT_EMAIL / JIRA_API_TOKEN
# /opt/agent-factory/secrets/github.env   (600) GITHUB_TOKEN
# /root/nourtech/deploy/prod/.env         (600) FACTORY_DB_PASSWORD, CAMILIA_BOOTSTRAP_PASSWORD
```

Variables lues par l'API : `JIRA_SITE_URL`, `JIRA_ACCOUNT_EMAIL`, `JIRA_API_TOKEN`,
`GITHUB_TOKEN`, `CAMILIA_GITHUB_OWNER` (défaut `epsil2`), `FACTORY_AUTH` (défaut `on`),
`CAMILIA_BOOTSTRAP_USER`/`CAMILIA_BOOTSTRAP_PASSWORD`, `CP_MAX_JOBS` (défaut 2).

`scripts/deploy_prod.sh` génère `CAMILIA_BOOTSTRAP_PASSWORD` dans `deploy/prod/.env`
s'il est absent (mode 600) et ne l'affiche jamais.

## API

```
GET    /api/v1/factory/catalogue
GET    /api/v1/factory/projects
POST   /api/v1/factory/projects            # rôle admin|operator
GET    /api/v1/factory/projects/{id}
PATCH  /api/v1/factory/projects/{id}       # verrouillé dès le provisioning
POST   /api/v1/factory/projects/{id}/spec
POST   /api/v1/factory/projects/{id}/spec/submit
POST   /api/v1/factory/projects/{id}/approve
POST   /api/v1/factory/projects/{id}/provision   # 202 + job de fond
POST   /api/v1/factory/projects/{id}/retry
POST   /api/v1/factory/projects/{id}/backlog     # 202
POST   /api/v1/factory/projects/{id}/development # 202, état READY requis
GET    /api/v1/factory/projects/{id}/executions
GET    /api/v1/factory/projects/{id}/audit
```

Le provisioning tourne dans un job de fond (`cp_jobs`, `CP_MAX_JOBS` bornés) : le
navigateur poll `GET /projects/{id}` toutes les 3 s, il n'attend jamais la fin.

## Spécification générée

Aucun modèle de langage n'est configuré sur cet hôte : le brouillon de spécification est
**assemblé de façon déterministe** à partir de la saisie (besoins, objectif, type,
template) et porte `generator: deterministic-draft`. Il doit être relu et édité avant
approbation ; il n'est jamais présenté comme produit par une IA.

## Diagnostic

```bash
docker logs --tail 100 agent-factory-prod-api-1        # erreurs de provisioning
curl -s localhost:8081/healthz | jq
psql -h 127.0.0.1 -U factory -d factory -c \
  "select doc->>'state', count(*) from cp_documents where kind='factory_provisioning' group by 1;"
```

- Opération en échec → la page projet affiche l'erreur exacte et `failed_step` ;
  « Relancer » rejoue l'étape seule.
- `REQUIRES_INTERVENTION` → l'erreur est non réessayable (droits, conflit de nom) : il
  faut corriger la cause côté Jira/GitHub, puis relancer.
- Hermes indisponible dans le conteneur API → l'étape `register_workspace` échoue en
  `BLOCKED` avec le message explicite ; rien n'est simulé.

## Credential GitHub : App d'abord, jeton personnel en secours

Le provisioning écrit dans le dépôt qu'il vient de créer. Un jeton personnel *fine-grained*
limité à des dépôts sélectionnés **crée** le dépôt (HTTP 201) mais n'obtient ensuite ni
lecture ni écriture dessus (`GET /repos/{owner}/{repo}` → 404, `git ls-remote` →
« Write access to repository not granted »). D'où la App GitHub comme credential machine.

Trois credentials distincts, à ne pas confondre :

| Credential | Sert à | Durée | Où il vit |
|---|---|---|---|
| `GITHUB_APP_ID` + clé privée `.pem` | signer un JWT RS256 → **jeton d'installation** | 1 h (renouvelé) | `/opt/agent-factory/secrets/github-app.env`, 0600 |
| `GITHUB_APP_CLIENT_ID` + `_CLIENT_SECRET` | **flux OAuth** : un humain autorise la App | code à usage unique | idem, 0600 |
| `GITHUB_TOKEN` (PAT) | secours, hôte sans App | — | `/opt/agent-factory/secrets/github.env` |

`github_credential()` renvoie le mode réellement utilisable (`app`, `pat`, `none`) et
`GET /api/v1/factory/github/status` l'expose : l'interface ne prétend jamais utiliser la App
quand c'est encore le jeton personnel.

**Un client secret seul n'est pas un credential machine** : il ne permet pas de signer le
JWT, donc pas d'obtenir de jeton d'installation, donc pas d'écriture. Il manque
`GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, et — après le flux OAuth —
`GITHUB_APP_INSTALLATION_ID`.

### Flux OAuth (lier l'installation)

1. `POST /api/v1/factory/github/oauth/start` → `authorize_url` + `state` (usage unique,
   lié à l'acteur vérifié, stocké en `cp_documents`).
2. L'humain autorise la App sur GitHub → retour sur
   `GET /api/v1/factory/github/oauth/callback?code&state`.
3. Le serveur échange le code, lit `GET /user/installations` **avec le jeton utilisateur**,
   retient l'installation du compte `CAMILIA_GITHUB_OWNER` et écrit
   `GITHUB_APP_INSTALLATION_ID` dans le fichier 0600.
4. Le jeton utilisateur n'est **ni stocké ni utilisé** pour le provisioning : le credential
   machine reste le jeton d'installation.

`state` rejoué → 400 ; `state` d'un autre utilisateur → 403 ; refus GitHub → redirection
`#factory?github=refused` avec l'audit `github.oauth_refused`.

### Vérifier le credential

```bash
cd /root/nourtech && .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "services/api")
from app import github_app
print(github_app.configured())
print(github_app.installation_identity("epsil2"))
PY
```

`installation_identity` renvoie l'id d'installation et le nombre de dépôts couverts ; une
erreur 404 signifie que la App n'est pas installée sur le compte cible.
