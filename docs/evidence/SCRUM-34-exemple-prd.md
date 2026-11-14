# PRD — Créer un agent qui prépare la release hebdomadaire de la Factory

*Ticket source : SCRUM-34*
## Contexte

Créer un agent qui prépare la release hebdomadaire de la Factory. Il ne doit jamais écrire dans Jira et les secrets doivent rester hors du dépôt.

## Objectif

Créer un agent qui prépare la release hebdomadaire de la Factory

## Périmètre
- Créer un agent qui prépare la release hebdomadaire de la Factory

## Hors périmètre
- aucune modification des workflows, types de tickets ou permissions Jira
- aucun appel à un fournisseur payant sans accord explicite

## Contraintes
- Il ne doit jamais écrire dans Jira et les secrets doivent rester hors du dépôt

## Risques
- dépendance externe détectée dans la demande : jira
- dépendance externe détectée dans la demande : secret

## Critères d'acceptation
- la demande « Créer un agent qui prépare la release hebdomadaire de la Factory » est satisfaite et vérifiable par un test
- le comportement est identique sur deux exécutions successives (déterministe)
- aucun secret, jeton ou donnée personnelle n'apparaît dans la sortie
- le journal d'audit contient une entrée pour chaque action réalisée
- toutes les contraintes explicites de la demande sont respectées

## Décisions de conception
- **aucune génération par modèle de langage à ce stade** — aucun fournisseur payant n'est autorisé ; le résultat doit être reproductible
- **la demande d'origine est conservée telle quelle** — elle sert de source de vérité en cas de désaccord sur la portée
