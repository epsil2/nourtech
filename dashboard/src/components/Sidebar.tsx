import type { ReactNode } from "react";

export type PageKey =
  | "overview"
  | "agents"
  | "backlog"
  | "sprints"
  | "github"
  | "releases"
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
  { key: "agents", label: "Agents", icon: I("M12 11a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 12 11zM5 20c1.5-4 4-6 7-6s5.5 2 7 6") },
  { key: "backlog", label: "Backlog Jira", icon: I("M4 6h16M4 12h16M4 18h10"), soon: true },
  { key: "sprints", label: "Sprints", icon: I("M4 5h16v15H4zM8 3v4M16 3v4"), soon: true },
  { key: "github", label: "GitHub", icon: I("M6 3v12a3 3 0 0 0 3 3h9M6 19a2.2 2.2 0 1 0 0 .1M18 18a2.2 2.2 0 1 0 0 .1"), soon: true },
  { key: "releases", label: "CI/CD & Releases", icon: I("M12 3v6M12 15v6M5 12H3M21 12h-2M12 15.4a3.4 3.4 0 1 0 0-6.8 3.4 3.4 0 0 0 0 6.8") },
  { key: "monitoring", label: "Monitoring", icon: I("M4 19V9M10 19V5M16 19v-7M22 19H2"), soon: true },
  { key: "settings", label: "Intégrations", icon: I("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-4l-.3 2.9a7 7 0 0 0-1.7 1L6 5.9 4 9.3 6 11a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.5-1a7 7 0 0 0 1.7 1L10.5 21h4l.3-2.9a7 7 0 0 0 1.7-1l2.5 1 2-3.4-2-1.6c.06-.33.1-.66.1-1z"), soon: true },
];

export function Sidebar({
  current,
  onNavigate,
  apiOk,
  apiLabel,
}: {
  current: PageKey;
  onNavigate: (key: PageKey) => void;
  apiOk: boolean | null;
  apiLabel: string;
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
      </div>
    </aside>
  );
}
