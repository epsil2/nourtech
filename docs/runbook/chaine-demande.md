# D'une demande business à toute la chaîne (SCRUM-54)

Un seul appel ouvre une demande, et la chaîne avance ensuite **d'elle-même** à chaque
validation humaine, sans recopier de payload entre les étapes.

## Le point d'entrée

```bash
curl -s -X POST http://127.0.0.1:8081/api/v1/requests \
  -H 'content-type: application/json' -H 'x-actor: soufiane' \
  -d '{"request":"Publier un radar IA hebdomadaire sur nourredine.tech avec 10 signaux",
       "source_key":"SCRUM-56","mirror_jira":true}'
```

Réponse `201` :

```json
{"request_id":"67acb162-…","workflow_id":"67acb162-…","stage":"discovery",
 "status":"awaiting_approval","waiting_for":"architecture_review",
 "jira":{"mirrored":true,"key":"SCRUM-56",…},
 "next":"POST /api/v1/workflows/67acb162-…/decision"}
```

`request_id` **est** l'id du workflow de découverte : il n'y a pas de table
supplémentaire, la chaîne se relit dans `state.chain` des runs.

`mirror_jira` est optionnel (défaut `false`) : quand il est vrai et que le chemin
d'écriture est ouvert, la story Jira est créée (ou réutilisée, label `ai-factory`).
Si Jira refuse, la demande est quand même enregistrée et la raison est conservée
dans `state.chain.jira.reason` : une panne d'intégration ne bloque pas une demande.

## Ce qui se déclenche, et à quel moment

```
POST /requests
   └─ discovery : intake → prd_draft → [architecture_review]  ← ARRÊT (humain)
                                        decision=approved
   └─ discovery : backlog_plan → completed
                       ↓ automatique (chain.on_decision)
   └─ delivery : plan → [implement]                            ← ARRÊT (humain)
                                        decision=approved
   └─ delivery : qa → [release_ready]                          ← ARRÊT (humain + preuve)
                                        evaluation acceptée + decision=approved
   └─ delivery : completed → état release préparé (stories, QA)
                       ↓ manuel, par construction
   └─ release : artifact CI → readiness → plan → approve → notes → publish
```

Trois règles à retenir :

1. **Les portes ne bougent pas.** Chaque étape se met en pause sur sa porte
   `approval=True`. Le chaînage supprime la recopie, pas les validations.
2. **La qualité reste une preuve.** Sans évaluation `passed` sur l'id du workflow de
   livraison, la porte `release_ready` répond `409 quality gate failed: no evaluation
   recorded for this workflow`. Aucun contournement automatique n'est prévu.
3. **La release ne se déclenche jamais seule.** `chain.release.prepared` liste ce qu'il
   manque : l'artefact du workflow CI (`commit_sha`). La plateforme ne s'invente pas un
   artefact, donc `readiness` répond `404 artifact not registered for this commit`
   tant qu'aucun build n'a été enregistré.

## Suivre une demande

```bash
curl -s http://127.0.0.1:8081/api/v1/requests/<request_id>
```

Renvoie `stage` (`discovery` / `delivery` / `release`), chaque étape avec son statut,
sa porte d'attente et **le prochain appel à faire**, l'état Jira, le handoff de release
(stories + QA), et l'audit consolidé des deux runs.

## Garde-fous vérifiés par les tests

- Rejouer une décision déjà honorée renvoie `409` et ne crée **pas** un second
  workflow de livraison (`tests/test_chain.py`).
- Un workflow démarré à la main (`POST /workflows`) n'est jamais chaîné : aucune
  surprise sur les runs existants.
- Une erreur de chaînage est auditée (`chain.failed`) mais ne transforme jamais une
  décision acceptée en erreur HTTP : `POST /workflows/{id}/decision` répond toujours le
  résultat du moteur.
- Une demande de moins de 20 caractères est refusée (`422`).

## Codes ajoutés

- `POST /api/v1/requests` → `201`
- `GET /api/v1/requests/{request_id}` → `200` | `404`
