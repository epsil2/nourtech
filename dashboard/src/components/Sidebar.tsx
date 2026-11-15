import type { ReactNode } from "react";

export type PageKey =
  | "overview"
  | "projects"
  | "factory"
  | "portfolio"
  | "agents"
  | "backlog"
  | "sprints"
  | "executions"
  | "github"
  | "releases"
  | "deployments"
  | "monitoring"
  | "settings";

type Item = { key: PageKey; label: string; icon: ReactNode; soon?: boolean };

const I = (d: string) => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d={d} />
  </svg>
);

export const NAV: Item[] = [
  { key: "overview", label: "Vue d'ensemble", icon: I("M3 12h7V3H3zM14 21h7v-9h-7zM14 3v6h7V3zM3 21h7v-6H3z") },
  { key: "projects", label: "Projets", icon: I("M4 6h6l1.6 2H20v10H4z") },
  { key: "factory", label: "Project Factory", icon: I("M3 21h18M5 21V10l5 3V10l5 3V6l5 4v11M8 21v-3h3v3M16 17h1M16 14h1") },
  { key: "portfolio", label: "Portefeuille projets", icon: I("M4 5h16v4H4zM4 11h7v8H4zM13 11h7v8h-7z") },
  { key: "agents", label: "Agents", icon: I("M12 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 12 11zM5 20c1.5-4 4-6 7-6s5.5 2 7 6") },
  { key: "backlog", label: "Backlog Jira", icon: I("M4 6h16M4 12h16M4 18h10") },
  { key: "sprints", label: "Sprints", icon: I("M4 5h16v15H4zM8 3v4M16 3v4") },
  { key: "executions", label: "Exécutions", icon: I("M8 5l11 7-11 7z") },
  { key: "github", label: "GitHub", icon: I("M6 3v12a3 3 0 0 0 3 3h9M6 19a2.2 2.2 0 1 0 0 .1M18 18a2.2 2.2 0 1 0 0 .1") },
  { key: "releases", label: "CI/CD & Releases", icon: I("M12 3v6M12 15v6M5 12H3M21 12h-2M12 15.4a3.4 3.4 0 1 0 0-6.8 3.4 3.4 0 0 0 0 6.8") },
  { key: "deployments", label: "Déploiements", icon: I("M12 4v9m0 0l-4-4m4 4l4-4M5 18h14") },
  { key: "monitoring", label: "Monitoring", icon: I("M4 19V9M10 19V5M16 19v-7M22 19H2") },
  { key: "settings", label: "Intégrations", icon: I("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-4l-.3 2.9a7 7 0 0 0-1.7 1L6 5.9 4 9.3 6 11a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.5-1a7 7 0 0 0 1.7 1L10.5 21h4l.3-2.9a7 7 0 0 0 1.7-1l2.5 1 2-3.4-2-1.6c.06-.33.1-.66.1-1z") },
];

export function Sidebar({
  current,
  onNavigate,
  apiOk,
  apiLabel,
  username,
  role,
  onSignOut,
}: {
  current: PageKey;
  onNavigate: (key: PageKey) => void;
  apiOk: boolean | null;
  apiLabel: string;
  username?: string;
  role?: string;
  onSignOut?: () => void;
}) {
  return (
    <aside>
      <div className="brand">
        <div className="logo" />
        <div>
          <b>Camilia</b>
          <span>Agent Factory</span>
        </div>
      </div>
      <nav>
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`nav ${current === item.key ? "on" : ""}`}
            onClick={() => onNavigate(item.key)}
          >
            {item.icon}
            {item.label}
            {item.soon && <span className="soon">S2</span>}
          </button>
        ))}
      </nav>
      <div className="status-card">
        <div>
          <span className="dot" style={{ background: apiOk ? "var(--ok)" : apiOk === false ? "var(--bad)" : "var(--mut-2)", boxShadow: apiOk ? "0 0 10px var(--ok)" : "none" }} />
          <b style={{ fontSize: 12.5 }}>{apiOk === null ? "Vérification…" : apiOk ? "API connectée" : "API injoignable"}</b>
        </div>
        <p>{apiLabel}</p>
        <div className="row">
          <span>Environnement</span>
          <span>prod · VPS</span>
        </div>
        <div className="row">
          <span>Projet Jira</span>
          <span>SCRUM</span>
        </div>
        {username && (
          <div className="row">
            <span>{username}</span>
            <span>{role ?? ""}</span>
          </div>
        )}
        {onSignOut && (
          <button className="btn ghost sm" type="button" onClick={onSignOut}>
            Se déconnecter
          </button>
        )}
      </div>
    </aside>
  );
}
