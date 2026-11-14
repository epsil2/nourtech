# Audit lecture seule — VPS srv2115787 (2.24.51.199)
Date: 2026-11-14 12:20 UTC · Hôte: Ubuntu 24.04.5 LTS, noyau 6.8.0-147-generic · uptime 12j
Auteur: Hermes (agent d'implémentation AI Agent Factory)
Portée: LECTURE SEULE. Aucun service arrêté, aucun conteneur redémarré, aucune config modifiée.

## 1. Capacité
| Ressource | Valeur | Commentaire |
|---|---|---|
| CPU | 1 vCPU | facteur limitant pour une stack factory + QA en parallèle |
| RAM | 3.8 Gi (2.0 utilisés, 153 Mi libres, 2.0 buff/cache) | marge faible: la stack factory doit être dimensionnée serrée |
| Swap | 2.0 Gi (977 Mi utilisés) | pression mémoire déjà réelle |
| Disque / | 48 G, 30 G utilisés (62%), 19 G libres | suffisant pour un DEV isolé, pas pour de gros builds répétés |

## 2. Conteneurs en production (ne pas toucher)
Compose projects actifs: hermes-web, nourredine-site, trading-platform, traefik, vibe-trading
- traefik-traefik-1 (traefik:latest, v3.7.17) — *:80 et *:443
- hermes-web-edge (nginx:alpine)
- nourredine-site-nourredine-site-1 / nourredine-site-contact-form-1
- trading-platform-{web,api,worker,db(postgres:16-alpine),redis:7-alpine}
- vibe-trading-vibe-trading-1

Aucun port 5432/6379 publié sur l'hôte (vérifié): `docker ps` ne montre que des ports internes, `ss -ltnp` ne liste ni 5432 ni 6379 en écoute publique. Bon point pour la règle « jamais publier Postgres/Redis ».

## 3. Ports en écoute (hôte)
22 sshd · 80/443 traefik · 631 cupsd · 5910/5900/3389 x11vnc/xrdp (loopback sauf 631) · 9223 chrome CDP (loopback) · 9119/9120/46061 hermes (loopback) · 53 systemd-resolve (loopback)
ufw: **inactive** (iptables géré ailleurs, probablement par le fournisseur/Traefik).

## 4. Services Hermes (systemd user)
- hermes-gateway.service (Telegram, profil « soku ») — running
- hermes-dashboard.service (dashboard edge-proxy, loopback) — running

## 5. État du projet « AI Agent Factory »
- `/opt/agent-factory/` : **absent**
- Dépôt Git dédié : **absent** (seul dépôt git de la machine: /root/.hermes/hermes-agent)
- `gh` CLI : **absent** → aucune capacité PR GitHub pour l'instant
- Connecteur Jira (OAuth géré par Hermes) : présent dans le catalogue, **non connecté**
- Aucune configuration Jira (site, projet SCRUM) sur la machine

## 6. Écarts par rapport au prompt d'exécution
1. Authentification Jira: impossible sans URL de site + email du compte (le jeton seul ne suffit pas pour Basic auth). Le connecteur Jira OAuth est la voie recommandée par le prompt (§ contrôles) et évite de faire circuler le jeton.
2. Preuve Git/PR: pas de dépôt ni de remote → les critères « pull request liée » ne peuvent pas être satisfaits sans décision sur l'hébergement du dépôt.
3. Capacité: 1 vCPU / 3.8 Gi RAM. Une stack complète (API + Postgres + Redis + dashboard + runner CI) sur cet hôte entre en concurrence avec trading-platform et Hermes. À cadrer avant le sprint 3.
4. Sprint assigné: inconnu — doit être lu dans Jira avant toute implémentation (règle « Select scope »).

## 7. Rotation du secret
Le jeton Jira a été collé dans une conversation (transcription conservée). Il doit être considéré comme exposé: rotation recommandée côté Atlassian après validation. Il a été rangé dans `/root/.hermes/secrets/jira.env` (chmod 600), hors dépôt, hors Jira, hors logs.

## 8. Commandes exécutées (toutes lecture seule)
docker ps / docker network ls / docker volume ls / docker version · ss -ltnp · df -h · free -h · nproc · uptime · systemctl --user list-units · du -sh /opt/* · crontab -l · ufw status

## 9. Reproduction

```bash
python3 scripts/vps_audit.py --json /tmp/baseline.json --markdown /tmp/baseline.md
```

The script is read-only by construction: `tests/test_vps_audit.py::test_collectors_never_issue_mutating_commands` fails if a collector ever issues anything but the allow-listed inspection commands.
