import { useEffect, useState } from "react";
import { Sidebar, NAV } from "./components/Sidebar";
import type { PageKey } from "./components/Sidebar";
import { Overview } from "./pages/Overview";
import { Soon } from "./pages/Soon";
import { api } from "./lib/api";

function readHash(): PageKey {
  const key = window.location.hash.replace("#", "") as PageKey;
  return NAV.some((n) => n.key === key) ? key : "overview";
}

export default function App() {
  const [page, setPage] = useState<PageKey>(readHash);
  const [health, setHealth] = useState<{ ok: boolean | null; label: string }>({ ok: null, label: "connexion à l'API…" });

  useEffect(() => {
    const onHash = () => setPage(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let alive = true;
    const check = () =>
      api
        .get<{ status?: string; env?: string }>("/healthz")
        .then((h) => alive && setHealth({ ok: true, label: `${h.status ?? "ok"} · env ${h.env ?? "—"} · store PgStore` }))
        .catch(() => alive && setHealth({ ok: false, label: "l'API ne répond pas — vérifier agent-factory-prod-api" }));
    check();
    const timer = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const go = (key: PageKey) => {
    window.location.hash = key;
    setPage(key);
  };

  const meta = NAV.find((n) => n.key === page);

  return (
    <div className="shell">
      <Sidebar current={page} onNavigate={go} apiOk={health.ok} apiLabel={health.label} />
      <main>
        {page === "overview" && <Overview onNewAgent={() => go("agents")} />}
        {page === "agents" && <Soon title="Agents" sprint="Sprint 2" body="Registre des agents, wizard de spécification en 8 sections, écran de revue et approbation du backlog Jira. Le socle de données est déjà servi par l'API." />}
        {page === "backlog" && <Soon title="Backlog Jira" sprint="Sprint 3" body="Epics, Features, Stories et Tasks du projet SCRUM lus en direct — et créés uniquement après votre approbation explicite." />}
        {page === "sprints" && <Soon title="Sprints" sprint="Sprint 4" body="Avancement par sprint, stories bloquées, dépendances et reste-à-faire, aligné sur le board 1." />}
        {page === "github" && <Soon title="GitHub" sprint="Sprint 4" body="Dépôts, branches liées aux clés Jira, commits et pull requests avec statut de revue." />}
        {page === "releases" && <Soon title="CI/CD & Releases" sprint="Sprint 5" body="Builds, tests, environnements DEV → QA → PRE-PROD → PROD, historique de déploiement et rollback. La promotion en prod reste soumise à votre Go/No-Go." />}
        {page === "monitoring" && <Soon title="Monitoring & KPI" sprint="Sprint 6" body="Exécutions, taux de succès, durées, consommation de jetons, coûts et erreurs API — calculés depuis la base, jamais estimés." />}
        {page === "settings" && <Soon title="Intégrations & réglages" sprint="Sprint 7" body="Jira, GitHub, fournisseurs LLM, credentials et permissions. Les secrets resteront côté serveur, jamais dans le frontend." />}
        {!meta && <Soon title="Introuvable" sprint="" body="Cette page n'existe pas." />}
      </main>
    </div>
  );
}
