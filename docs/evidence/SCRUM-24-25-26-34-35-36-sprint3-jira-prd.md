# Preuve — Sprint 3 : intégration Jira lecture seule et PRD versionné

Date : 2026-11-14 · Branche : `feature/sp3-jira-prd` · Base : `65f821b`

## Stories couvertes

- **SCRUM-24** — découverte Jira et mapping de schéma (lecture seule)
- **SCRUM-36** — un administrateur peut découvrir le schéma Jira avant d'autoriser la Factory
- **SCRUM-25** — génération d'un PRD métier versionné (déterministe, sans fournisseur payant)
- **SCRUM-34** — une demande métier devient un PRD approuvable
- **SCRUM-26** — décisions d'architecture (`docs/adr/0003-jira-read-only-and-deterministic-prd.md`)
- **SCRUM-35** — traçabilité décision → ticket source (`source_key`)

## 1. Le client Jira est prouvé lecture seule

`services/api/app/jira.py` n'expose que des lectures. Deux tests indépendants :

1. **Test de transport** (`tests/test_jira_integration.py`) : un `opener` factice enregistre
   chaque requête émise ; le test échoue si une méthode autre que `GET` apparaît.
2. **Test de source** : le fichier est analysé et le test échoue si un verbe mutant
   (`put`, `post`, `patch`, `delete`) y est présent.

Aucun jeton n'est retourné par une méthode publique ni écrit dans un log : la valeur du
jeton n'apparaît que dans l'en-tête `Authorization` construit à l'appel.

## 2. Exécution réelle contre `https://nourtech.atlassian.net`

    configured True
    {'account_id': '70121:2a71469a-...', 'display_name': 'Epsilon Nour',
     'time_zone': 'Europe/Paris'}

Inventaire lu par ce même client, sans aucune écriture :

- board `1` → 2 sprints (`SCRUM Sprint 0` actif du 2026-11-13 au 2026-11-27, `SCRUM Sprint 1` futur)
- projet `SCRUM` → **51 tickets** lus (`project=SCRUM ORDER BY key ASC`)
- 12 epics (`SCRUM-5` … `SCRUM-16`) et 39 stories/tâches

## 3. PRD déterministe

`services/api/app/prd.py` produit un PRD sectionné (contexte, objectif, périmètre,
hors périmètre, contraintes, risques, critères d'acceptation, traçabilité) à partir d'une
demande libre. Aucun appel réseau, aucun LLM payant : la même entrée donne le même
document, ce qui rend la preuve rejouable.

Exemple réel versionné : `docs/evidence/SCRUM-34-exemple-prd.md`.

Une phrase de contrainte (« ne doit jamais écrire dans Jira ») est classée
**uniquement** dans *Contraintes* : la dupliquer dans *Périmètre* la rendrait lisible
comme optionnelle.

Une demande de moins de 15 caractères ou de moins de 3 mots est refusée par `ValueError`.

## 4. Surfaces HTTP

| Route | Rôle |
|---|---|
| `GET /api/v1/integrations/jira/status` | `configured`, `site`, `read_only: true` |
| `GET /api/v1/integrations/jira/schema` | schéma projet + board (503 non configuré, 502 amont) |
| `GET /api/v1/integrations/jira/issues` | lecture de tickets, jamais de mutation |
| `POST /api/v1/prd` | création d'un PRD rattaché à un `source_key` |
| `GET /api/v1/prd/{id}` et `…/markdown` | lecture et rendu |
| `POST /api/v1/prd/{id}/approve` | approbation **auditée** |

## 5. Résultat des tests

    $ .venv/bin/python -m pytest -q
    66 passed in 0.46s
    $ .venv/bin/python scripts/secret_scan.py .
    secret scan: ok

## Ce qui n'a pas été fait

- Aucune écriture Jira : pas de commentaire, pas de transition, aucun changement de
  workflow, de type de ticket, de board ou de permission.
- Aucun fournisseur payant appelé.
