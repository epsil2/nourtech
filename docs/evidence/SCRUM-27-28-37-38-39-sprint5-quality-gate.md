# Sprint 5 — plan lié à la PR, évaluation déterministe, gate qui bloque la release

Date : 2026-11-14. Branche : `feature/sp5-plan-and-quality-gate`.
Chaîne : DEV (push `main`) → Qualité → Production (tag `SP5-Release`).

## Ce que les stories demandent, et où c'est tenu

| Story | Exigence | Pièce |
| --- | --- | --- |
| SCRUM-27 | générer un plan de développement à partir d'une story | `services/api/app/planner.py` |
| SCRUM-37 | lier le plan à une branche et à une pull request | `planner.branch_name`, `POST /plans/{id}/link` |
| SCRUM-38 | le plan est la trace du travail, pas une note libre | `plans` (statut, `approved_by`, `pull_request_url`) |
| SCRUM-28 | un gate qualité échoué bloque la release | `workflow._release_ready` + `latest_evaluation` |
| SCRUM-39 | l'évaluation est reproductible et relue par un humain | `evaluation.py`, `POST /evaluations/{id}/review` |

## Le plan

`POST /api/v1/plans` construit, sans appel modèle, à partir de `issue_key`, `summary`
et `description` :

- une branche `feature/scrum-<n>-<slug>` ;
- un périmètre de fichiers déduit de mots-clés (`api`, `workflow`, `jira`, `prd`,
  `audit`, `deploy`, …) ; une story sans mot-clé reconnu retombe sur
  `services/api/app/` + `tests/`, jamais sur tout le dépôt ;
- cinq étapes avec leur critère de fin, et quatre familles de tests obligatoires.

Ordre imposé : `draft` → `approved` (acte humain, `approved_by` enregistré) →
`linked` (`pull_request_url` validée contre `https://github.com/epsil2/nourtech/pull/<n>`).
Lier une PR avant approbation renvoie **409**, une URL hors dépôt **422**.

## L'évaluation

Deux jeux de cas déclarés par `GET /api/v1/evaluations/datasets` :

- `quality` : substance (≥ 40 caractères), structure (≥ 3 lignes), présence de
  critères d'acceptation (poids 2), absence de `TODO`/`FIXME`/`lorem ipsum` ;
- `safety` : aucun secret (motifs de `redaction.py`, les mêmes qui masquent l'audit)
  et résistance à l'injection — deux cas **critiques**.

Le score est `somme(poids réussis) / somme(poids)`. `passed = score >= threshold` et
**aucun échec critique**. Le même texte donne toujours le même score.

## Le gate

```
quality gate failed: no evaluation recorded for this workflow      -> 409
quality gate failed: evaluation 7 scored 0.4 below threshold       -> 409
```

Un gate bloqué laisse le workflow en `awaiting_approval` sur ce nœud. Dès qu'une
évaluation réussie est enregistrée pour ce workflow, la même décision repasse : le
gate recalcule depuis la base, il ne relit pas le snapshot du nœud `qa`.

## Preuves exécutées

```
$ pytest -q
107 passed in 0.96s

$ python scripts/secret_scan.py .
secret scan: ok
```

Sonde sur un PostgreSQL 16 réel (conteneur jetable `af-probe5`, `127.0.0.1:55433`,
supprimé ensuite) :

```
plan draft SCRUM-99
eval 1 1.0 float True
wf1 awaiting_approval implement
wf2 awaiting_approval release_ready False
blocked: quality gate failed: no evaluation recorded for this workflow
wf3 completed True checkpoints: 9
audit 7
```

Cette sonde a trouvé un vrai défaut : `subject = %s` comparait `text` à un `uuid`
(psycopg renvoie l'identifiant PostgreSQL en `uuid.UUID`), ce qui rendait le gate
inutilisable en production alors que les tests mémoire passaient. Corrigé par
`%s::text` côté SQL et `str(workflow["id"])` côté moteur.
