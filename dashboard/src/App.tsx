import { useEffect, useState } from "react";
import { Sidebar, NAV } from "./components/Sidebar";
import type { PageKey } from "./components/Sidebar";
import { Agents } from "./pages/Agents";
import { Deployments } from "./pages/Deployments";
import { Executions } from "./pages/Executions";
import { Projects } from "./pages/Projects";
import { Backlog } from "./pages/Backlog";
import { GitHub } from "./pages/GitHub";
import { Integrations } from "./pages/Integrations";
import { Monitoring } from "./pages/Monitoring";
import { Overview } from "./pages/Overview";
import { Releases } from "./pages/Releases";
import { Soon } from "./pages/Soon";
import { Sprints } from "./pages/Sprints";
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
        .get<{ status?: string; env?: string; store?: string }>("/healthz")
        .then((h) => alive && setHealth({ ok: true, label: `${h.status ?? "ok"} · env ${h.env ?? "—"} · store ${h.store === "memory" ? "memory (volatile)" : "PostgreSQL"}` }))
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
        {page === "projects" && <Projects />}
        {page === "agents" && <Agents />}
        {page === "backlog" && <Backlog />}
        {page === "sprints" && <Sprints />}
        {page === "github" && <GitHub />}
        {page === "executions" && <Executions />}
        {page === "releases" && <Releases />}
        {page === "deployments" && <Deployments />}
        {page === "monitoring" && <Monitoring />}
        {page === "settings" && <Integrations />}
        {!meta && <Soon title="Introuvable" sprint="" body="Cette page n'existe pas." />}
      </main>
    </div>
  );
}
