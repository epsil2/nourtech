# ADR 0003 — Jira en lecture seule, PRD déterministe

Statut : accepté · Date : 2026-11-14

## Contexte

La Factory doit lire le schéma et les tickets Jira pour produire un PRD traçable, sans
jamais modifier l'outil de travail de l'équipe, et sans dépendre d'un fournisseur payant
pour une étape qui doit rester rejouable et auditable.

## Décision

1. **Le client Jira est lecture seule par construction.** `app/jira.py` n'expose aucune
   méthode mutante et n'accepte que `GET`. Deux tests le garantissent : un test de
   transport (les requêtes émises sont inspectées) et un test de source (les verbes
   mutants sont refusés dans le fichier). Une relecture de code ne suffit pas : la
   garantie doit casser la CI si quelqu'un ajoute un `POST`.
2. **Le PRD est déterministe.** La génération est un traitement local, sans réseau ni
   LLM. La même demande produit le même document, donc la preuve est rejouable et le
   coût nul.
3. **La traçabilité passe par `source_key`.** Chaque PRD porte le ticket Jira qui l'a
   motivé, et l'approbation écrit une ligne d'audit déjà rédigée.

## Conséquences

- Une demande de PRD enrichie par un LLM exigera un ADR ultérieur et un accord explicite
  sur le coût.
- Les mises à jour de Jira (commentaire, transition) restent des gestes humains ou des
  appels hors du client de production, jamais une capacité de l'API.
- Une phrase de contrainte est classée dans *Contraintes* seulement : la placer aussi
  dans *Périmètre* la rendrait lisible comme optionnelle.
