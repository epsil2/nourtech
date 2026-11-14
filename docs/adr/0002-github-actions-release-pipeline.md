# ADR 0002 — Chaîne de release sur GitHub Actions

- Statut : accepté
- Date : 2026-11-14
- Remplace : version GitLab de ce même ADR

## Contexte

La factory doit livrer depuis `epsil2/nourtech` vers la stack `agent-factory-prod`
du VPS, avec une étape DEV, une étape qualité, puis une porte humaine avant la
production. GitLab.com était la cible initiale, mais aucun accès GitLab n'existe
sur la machine et le propriétaire a tranché pour GitHub.

Le jeton GitHub disponible ne peut pas écrire de secret Actions
(`GET /repos/.../actions/secrets` → 403), ni enregistrer de runner, ni créer
d'environnement protégé. Un déploiement par clé SSH stockée en secret était donc
impossible sans intervention manuelle, et il aurait de toute façon mis une clé
root dans un dépôt public.

## Décision

Un seul pipeline, `.github/workflows/deploy.yml`, chaîne `dev` → `qualite` →
`production`, et un canal de release **par tag**, pas par clé :

1. **DEV obligatoire** : le job `dev` construit l'image et démarre la vraie stack
   DEV sur le runner, puis interroge `/healthz` et `/readyz`. Une image qui ne
   sert pas ne franchit jamais les gates.
2. **Qualité obligatoire** : `qualite` (`needs: dev`) rejoue tests,
   `docker compose config` sur les deux stacks et le scan de secrets ;
   `production` déclare `needs: qualite`.
3. **Porte humaine** : `workflow_dispatch` avec `confirm=deploy`, environnement
   `production`, branche `main`, `concurrency` verrouillée.
4. **Pull deploy vérifié** : le job publie le tag immuable `release/<sha>` ;
   `factory-release-watcher.timer` sur le VPS déploie ce sha (après avoir vérifié
   qu'il est un ancêtre de `main`) avec `scripts/deploy_prod.sh`, et renvoie le
   résultat en statut de commit `factory/production`. Le job attend ce statut :
   un déploiement raté rend le run rouge.

## Conséquences

- Aucun secret GitHub n'est nécessaire : ni clé SSH, ni registre, ni jeton de
  plateforme. Le seul jeton est celui du VPS, dans un fichier 600.
- Aucun runner n'a d'accès shell, docker ou root au VPS ; la surface d'attaque
  côté GitHub se limite à un tag, ce qui est aussi une trace d'audit lisible.
- Le résultat est asynchrone (≤ 60 s) mais vérifié : le run ne prétend jamais
  avoir déployé sans statut `success` du serveur.
- Le rollback reste dans `deploy_prod.sh` (tag précédent), identique en manuel et
  en pipeline.
- Le chemin GitLab (`.gitlab-ci.yml`, miroir, ADR précédent) est supprimé : une
  configuration morte qui ne peut pas s'exécuter est un piège.
