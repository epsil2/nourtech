# ADR 0005 — A release gate that reads evidence instead of trusting state

- Statut : accepté
- Date : 2026-11-14
- Stories : SCRUM-28, SCRUM-39

## Contexte

Le moteur de workflow (ADR 0004) a un nœud `qa` qui marquait ses vérifications comme
réussies parce que le backlog était bien formé. Ce n'est pas un contrôle qualité :
c'est une tautologie. Une release pouvait donc être approuvée sans qu'aucune preuve
n'existe, ce que le projet interdit explicitement.

## Décision

1. L'évaluation est un objet persistant et déterministe (`evaluation.py`) : jeux de
   cas `quality` et `safety`, score arithmétique, seuil explicite, aucun appel modèle.
2. Les cas `safety` sont **critiques** : les rater fait échouer l'évaluation quel que
   soit le score, sinon trois victoires cosmétiques masqueraient un secret fuité.
3. Le nœud `qa` **et** le nœud de gate `release_ready` calculent la qualité à partir de
   la dernière évaluation enregistrée pour ce workflow (`latest_evaluation`). Le gate
   ne fait pas confiance au snapshot du nœud précédent.
4. Un gate qui échoue laisse le workflow en `awaiting_approval` sur ce gate : la
   décision est refusée en 409, le run n'est pas détruit, et il se rouvre dès que la
   preuve manquante existe — sans redémarrer le workflow.
5. Un humain peut réviser un score (`/evaluations/{id}/review`) ; le verdict est
   journalisé dans l'audit et ne modifie pas le score calculé.

## Conséquences

- Une release ne peut plus être approuvée sans preuve : le 409 nomme l'évaluation
  absente ou son score.
- Approbation et preuve sont deux actes séparés et auditables : approuver un gate
  bloqué est enregistré (`workflow.approved`), puis refusé par le gate (`409`).
- Coût : deux tables (`plans`, `evaluations`) et un score qui ne remplace pas le
  jugement humain — il conditionne seulement le déclenchement du gate.

## Révision

Si un fournisseur d'évaluation externe est approuvé, il devra produire le même
contrat (score 0-1, seuil, échecs critiques) pour que le gate reste stable.
