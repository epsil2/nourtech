# Premier cas d'usage métier — « Radar IA hebdomadaire »

Exécuté le 2026-11-14 sur la prod (`http://127.0.0.1:8081`, PostgreSQL + Redis, image `41bcc13`).
Le besoin métier réel utilisé : **publier chaque lundi un article de veille IA sur
nourredine.tech — 10 signaux sourcés, 800 mots, en ligne avant 8h.**

Ce cas traverse les trois boucles de la plateforme et laisse une trace en base (audit).

## 0. Accès

L'API n'écoute que sur la boucle locale du VPS. Depuis un poste :

```bash
ssh -L 8081:127.0.0.1:8081 root@2.24.51.199
export BASE=http://127.0.0.1:8081
curl -s $BASE/readyz      # {"status":"ready", ...}
```

Toute requête qui « décide » porte l'auteur : `-H 'x-actor: soufiane'`.

## 1. Le besoin devient un PRD versionné

```bash
curl -s -X POST $BASE/api/v1/prd -H 'content-type: application/json' -H 'x-actor: soufiane' -d '{
 "request": "Publier chaque lundi un article de veille IA sur nourredine.tech : 10 signaux sourcés, 800 mots, en ligne avant 8h, avec liens vers les sources.",
 "source_key": "SCRUM-50"}'
# 201 {"id":"c5047e0f-…","status":"draft","payload":{...}}
```

Le PRD est stocké et relisible en markdown :

```bash
curl -s $BASE/api/v1/prd/<prd_id>/markdown        # 200, ~1 500 caractères
curl -s -X POST $BASE/api/v1/prd/<prd_id>/approve -H 'content-type: application/json' \
     -H 'x-actor: soufiane' -d '{"decision":"approve"}'      # 200 {"status":"approved"}
```

**Ce qu'il faut savoir sur ce PRD** : il est généré de façon déterministe (aucun appel
LLM). Il structure la demande en Contexte / Objectif / Périmètre / Risques et reprend
le texte fourni. C'est un squelette opposable, pas une analyse de marché : la valeur
vient de ce que vous écrivez dans `request`, et l'enrichissement de fond se fait à la
main ou par un agent à l'extérieur de la plateforme.

## 2. Le plan de dev est rattaché à la story

```bash
curl -s -X POST $BASE/api/v1/plans -H 'content-type: application/json' -H 'x-actor: soufiane' -d '{
 "issue_key":"SCRUM-50","summary":"Radar IA hebdomadaire",
 "description":"Collecte 10 signaux, rédaction, publication lundi 8h"}'   # 201
curl -s -X POST $BASE/api/v1/plans/<plan_id>/approve -H 'x-actor: soufiane'   # 200
```

## 3. Les portes humaines du workflow de livraison

```bash
curl -s -X POST $BASE/api/v1/workflows -H 'content-type: application/json' -H 'x-actor: soufiane' -d '{
 "definition":"delivery",
 "input":{"backlog":{"items":[
   {"index":1,"acceptance":"10 signaux collectés et sourcés"},
   {"index":2,"acceptance":"article de 800 mots relu"},
   {"index":3,"acceptance":"publication avant lundi 8h"}]}}}'
# 200 {"id":"58f6c559-…","status":"awaiting_approval","current_step":"implement"}
```

Rien n'avance sans décision explicite : c'est ici qu'un humain code. Puis :

```bash
curl -s -X POST $BASE/api/v1/workflows/<wf_id>/decision -H 'content-type: application/json' \
     -H 'x-actor: soufiane' -d '{"decision":"approved","note":"on code"}'
# 200 {"current_step":"release_ready"}
```

## 4. Sans preuve QA, la porte refuse

```bash
curl -s -X POST $BASE/api/v1/workflows/<wf_id>/decision -H 'content-type: application/json' \
     -d '{"decision":"approved"}'
# 409 {"detail":"quality gate failed: no evaluation recorded for this workflow"}
```

On enregistre la preuve, on la fait relire, et la porte s'ouvre :

```bash
curl -s -X POST $BASE/api/v1/evaluations -H 'content-type: application/json' -d '{
 "subject":"<wf_id>","dataset":"quality",
 "output":"## Livraison\n10 signaux sourcés\nAcceptance criteria: signé\n","threshold":0.8}'
# 201 {"id":3,"score":1.0,"passed":true}
curl -s -X POST $BASE/api/v1/evaluations/3/review -H 'content-type: application/json' \
     -H 'x-actor: soufiane' -d '{"outcome":"accepted"}'      # 200
curl -s -X POST $BASE/api/v1/workflows/<wf_id>/decision -H 'content-type: application/json' \
     -d '{"decision":"approved"}'                            # 200 {"status":"completed"}
```

## 5. La release reste impossible sans artefact CI

```bash
curl -s -X POST $BASE/api/v1/releases/readiness -H 'content-type: application/json' -d '{
 "commit_sha":"1111111111111111111111111111111111111111","target":"dev",
 "stories":[{"key":"SCRUM-50","summary":"Radar IA","status":"Done","type":"Story"}],"blockers":[]}'
# 404 {"detail":"artifact not registered for this commit"}
```

C'est le comportement voulu : un commit qui n'a pas été construit par le workflow
GitHub Actions `ci` ne peut pas être promu. Le jour où ce commit existe :

```bash
# 1. CI publie l'artefact
POST /api/v1/artifacts  {"version":"0.6.0","commit_sha":"<40 hex>","tests":{"passed":116,"failed":0},
                         "stories":[…],"workflow":"ci","source":"github",…}
# 2. une évaluation dont le sujet est le commit
POST /api/v1/evaluations {"subject":"<40 hex>","dataset":"quality","output":"…","threshold":0.8}
# 3. la chaîne
GET  /api/v1/artifacts/<40 hex>/verify     -> {"verified": true}
POST /api/v1/releases/readiness            -> {"ready": true, "missing": []}
POST /api/v1/releases/plan                 -> {"status":"draft"}
POST /api/v1/releases/<id>/approve  (x-actor) -> approved
POST /api/v1/releases/<id>/notes
POST /api/v1/releases/<id>/publish          -> published
```

## 6. Ce qui a été observé

- PRD `c5047e0f-…` créé puis approuvé ; markdown de 1 515 caractères.
- Plan `f1fa215b-…` approuvé, workflow `58f6c559-…` allé de `implement` à `done`.
- Porte `release_ready` : **409 sans preuve**, 200 avec évaluation `accepted` (score 1.0).
- `readiness` et `plan` : **404** sur un commit sans artefact — le garde-fou fonctionne.
- `GET /api/v1/observability/resources` → `audit_events: 32`, `artifacts: 0`, `releases: 0`.
- Toutes les décisions sont horodatées avec `x-actor` dans `audit_events`.

## 7. Pour aller plus loin sur ce cas

1. **Jira est branché** depuis le 2026-11-14 (lecture et écriture, projet `SCRUM`).
   La story de ce cas existe : **SCRUM-52** « Publier un radar IA hebdomadaire sur
   nourredine.tech ». Pour rattacher une demande à un ticket existant :

   ```bash
   curl -s -G $BASE/api/v1/integrations/jira/issues \
        --data-urlencode 'jql=key = SCRUM-52'                     # statut réel
   curl -s -X POST $BASE/api/v1/integrations/jira/issues/SCRUM-52/comment \
        -H 'content-type: application/json' -H 'x-actor: soufiane' \
        -d '{"text":"PRD validé — /api/v1/prd/<id>/markdown"}'
   curl -s -X POST $BASE/api/v1/integrations/jira/issues/SCRUM-52/transition \
        -H 'content-type: application/json' -H 'x-actor: soufiane' \
        -d '{"target":"In Progress"}'
   ```

   Détails et verrous : `docs/runbook/jira-lecture-ecriture.md`.
2. **Produire le vrai artefact** depuis GitHub Actions (workflow nommé `ci`), sinon la
   chaîne release restera fermée.
3. **Enrichir le PRD** : la génération est un gabarit. Si vous voulez des critères
   d'acceptation et des risques réels, c'est le point d'entrée à améliorer
   (`services/api/app/prd.py`).
