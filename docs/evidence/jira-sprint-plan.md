# Jira sprint split — AI Factory (SCRUM, board 1)

Date : 2026-11-14 · Projet team-managed `SCRUM` (nourtech.atlassian.net) · 35 items (22 stories, 13 tâches)

## Objectif

Toutes les user stories et leurs tâches d'implémentation sont réparties sur 7 sprints numérotés et
thématiques, dans l'ordre de livraison. Aucun item métier ne reste hors sprint.

## Répartition

| Sprint | État | Items | Contenu |
|---|---|---|---|
| Sprint 0 — historique (essais Jira) | closed | SCRUM-1, SCRUM-2 | Placeholders de test antérieurs, conservés |
| Sprint 1 — Fondations, API et dépôts | closed | 17, 19, 21, 22, 32 | Baseline VPS, isolation Compose, miroir GitLab, API FastAPI + PostgreSQL |
| Sprint 2 — Jira, PRD et conception technique | closed | 24, 25, 26, 34, 35, 36 | Découverte Jira en lecture seule, PRD déterministe, ADR et design technique |
| Sprint 3 — Orchestration, agent dev et QA | closed | 23, 27, 28, 31, 33, 37, 38, 39, 41 | LangGraph avec checkpoints, plan lié à la PR, harnais QA, audit et métriques, dashboard runs |
| Sprint 4 — Registre d'agents et gouvernance des dépôts | active | 18, 44, 45, 46 | Registre d'agents, pause/reprise/retrait, protection de branches et gates |
| Sprint 5 — CI/CD, releases et traçabilité des artefacts | future | 29, 40, 47, 48, 49 | Déploiement DEV avec rollback, artefacts immuables, notes de release |
| Sprint 6 — Supervision, audit et métriques | future | 42, 43, 50, 51 | Traçabilité auditable, redaction, fiabilité et coût par run |
| Sprint 7 — Intégration dashboard et durcissement | future | 20, 30 | Flux dashboard sur l'API, re-test de l'isolation et des contrôles de sécurité |

## Historique de livraison

- Sprints 1 à 3 : 20 items, tous `Done` (sprints clos).
- Sprints 4 à 7 : 15 items, tous `To Do` sauf exceptions — cohérent avec le backlog restant.

## Points d'attention

- `SCRUM-42` et `SCRUM-43` portent le même résumé (audit/resource) : doublon probable, à fusionner, tous deux en Sprint 6.
- `SCRUM-1`, `SCRUM-2`, `SCRUM-3` (« Task 1/2/3 ») sont des placeholders de test ; seuls 1 et 2 sont rattachés (Sprint 0), `SCRUM-3` reste hors sprint.
- Les anciens sprints `SCRUM Sprint 0/1/2` créés par automatisation ont été supprimés ou renommés : le board ne contient plus que Sprint 0 (historique) et Sprint 1 à 7.

## Contrainte technique constatée

Dans ce projet team-managed, un item ne peut être rattaché qu'à un sprint `active` ou `future` ;
un sprint clos ne peut pas être rouvert par l'API. Les sprints historiques ont donc été créés,
alimentés, puis clos dans l'ordre.
