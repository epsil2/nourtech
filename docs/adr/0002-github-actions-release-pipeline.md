# ADR 0002 — Pipeline de release sur GitHub Actions

- Statut : accepté
- Date : 2026-11-14
- Remplace : version GitLab de ce même ADR

## Contexte

La factory doit livrer depuis `epsil2/nourtech` vers la stack `agent-factory-prod`
du VPS, avec une étape qualité puis une porte humaine avant la production.
GitLab.com était la cible initiale, mais aucun accès GitLab (jeton, projet, runner)
n'existe sur la machine ; le propriétaire a tranché pour GitHub.

## Décision

Un seul pipeline, `.github/workflows/deploy.yml`, en trois garanties :

1. **Qualité obligatoire** : le job `production` déclare `needs: qualite`, qui
   rejoue tests, `docker compose config` sur les deux stacks et le scan de secrets.
2. **Porte humaine** : `workflow_dispatch` avec `confirm=deploy`, plus
   l'environnement GitHub `production` (required reviewers), branche `main`
   seulement, `concurrency` pour interdire deux déploiements simultanés.
3. **Surface minimale côté serveur** : le runner n'a ni docker, ni accès système.
   Il pousse `git archive` par SSH sur une clé restreinte dont la commande forcée
   n'accepte que `deploy <sha>` et délègue au `scripts/deploy_prod.sh` déjà testé.
   La clé d'hôte est épinglée dans `deploy/prod/known_hosts` (information publique,
   pas un secret).

## Conséquences

- Aucun secret de type registre, ni jeton de plateforme, n'est nécessaire : une
  seule clé SSH, stockée en secret d'environnement.
- Les artefacts GitLab (`/etc/gitlab-ci` du plan initial, `.gitlab-ci.yml`, miroir)
  sont supprimés : une configuration morte qui ne peut pas s'exécuter est un piège.
- Le rollback reste dans `deploy_prod.sh` (tag précédent), donc identique en
  manuel et en pipeline.
- Le déploiement direct depuis un runner hébergé exige que le VPS accepte SSH
  root par clé ; l'accès mot de passe reste désactivé.
