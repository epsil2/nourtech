import type { ReactNode } from "react";

export type PageKey =
  | "overview"
  | "portfolio"
  | "agents"
  | "backlog"
  | "sprints"
  | "executions"
  | "factory"
  | "github"
  | "releases"
  | "deployments"
  | "monitoring"
  | "settings";

type Item = { key: PageKey; label: string; icon: ReactNode; soon?: boolean; count?: number };

const I = (d: string) => (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d={d} />
  </svg>
);

export const NAV_GROUPS: { label: string; items: Item[] }[] = [
  {
    label: "Pilotage",
    items: [
      { key: "overview", label: "Vue d'ensemble", icon: I("M4 13h6V4H4zM14 20h6v-9h-6zM14 4v4h6V4zM4 20h6v-4H4z") },
      { key: "portfolio", label: "Portefeuille projets", icon: I("M3 7h7l2 2h9v10H3zM3 7V5h5l2 2") },
      { key: "agents", label: "Agents", icon: I("M12 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM5 20c1.4-3.6 3.8-5.4 7-5.4S17.6 16.4 19 20") },
    ],
  },
  {
    label: "Ingénierie",
    items: [
      { key: "backlog", label: "Backlog Jira", icon: I("M4 6h16M4 12h16M4 18h9") },
      { key: "sprints", label: "Sprints", icon: I("M4 5h16v15H4zM8 3v4M16 3v4M4 10h16") },
      { key: "executions", label: "Exécutions", icon: I("M8 5l11 6.6L8 18z") },
      { key: "factory", label: "Project Factory", icon: I("M3 20h18M5 20V10l5 2.6V10l5 2.6V5l4 3v12M8 20v-2.4h3V20") },
    ],
  },
  {
    label: "Livraison",
    items: [
      { key: "github", label: "GitHub", icon: I("M6 3v11a3 3 0 0 0 3 3h9M6 19a2.2 2.2 0 1 0 .1 0M18 17a2.2 2.2 0 1 0 .1 0") },
      { key: "releases", label: "CI/CD & Releases", icon: I("M12 3v5M12 16v5M5 12H3M21 12h-2M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4") },
      { key: "deployments", label: "Déploiements", icon: I("M12 4v9m0 0-4-4m4 4 4-4M5 19h14") },
      { key: "monitoring", label: "Monitoring", icon: I("M4 19V9M10 19V5M16 19v-6M22 19H2") },
    ],
  },
  {
    label: "Administration",
    items: [
      {
        key: "settings",
        label: "Intégrations",
        icon: I(
          "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.2 12c0-.3 0-.6-.06-.9l1.9-1.5-1.9-3.3-2.3 1a6.6 6.6 0 0 0-1.6-.9L14.9 3.6h-3.9l-.4 2.8c-.6.2-1.1.5-1.6.9l-2.3-1L4.8 9.6l1.9 1.5c-.04.3-.06.6-.06.9s0 .6.06.9l-1.9 1.5 1.9 3.3 2.3-1c.5.4 1 .7 1.6.9l.4 2.8h3.9l.4-2.8c.6-.2 1.1-.5 1.6-.9l2.3 1 1.9-3.3-1.9-1.5c.04-.3.06-.6.06-.9z",
        ),
      },
    ],
  },
];

export const NAV: Item[] = NAV_GROUPS.flatMap((group) => group.items);

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

      {NAV_GROUPS.map((group) => (
        <div className="nav-group" key={group.label}>
          <div className="nav-group__label">{group.label}</div>
          {group.items.map((item) => (
            <button
              key={item.key}
              className={`nav ${current === item.key ? "on" : ""}`}
              onClick={() => onNavigate(item.key)}
              type="button"
            >
              {item.icon}
              {item.label}
              {typeof item.count === "number" && <span className="count">{item.count}</span>}
              {item.soon && <span className="soon">S2</span>}
            </button>
          ))}
        </div>
      ))}

      <div className="status-card">
        <div style={{ display: "flex", alignItems: "center" }}>
          <span
            className="dot"
            style={{
              background: apiOk ? "var(--ok)" : apiOk === false ? "var(--bad)" : "var(--mut-2)",
              boxShadow: apiOk ? "0 0 10px var(--ok)" : "none",
            }}
          />
          <b style={{ fontSize: 11.5 }}>{apiOk === null ? "Vérification…" : apiOk ? "API connectée" : "API injoignable"}</b>
        </div>
        <p>{apiLabel}</p>
        <div className="row">
          <span>Environnement</span>
          <b>prod · VPS</b>
        </div>
        <div className="row">
          <span>Projet Jira</span>
          <b>SCRUM</b>
        </div>
        {username && (
          <div className="row">
            <span>{username}</span>
            <b>{role ?? ""}</b>
          </div>
        )}
        {onSignOut && (
          <button className="btn ghost sm" type="button" onClick={onSignOut} style={{ marginTop: 10, width: "100%" }}>
            <span className="pict">⏻</span> Se déconnecter
          </button>
        )}
      </div>
    </aside>
  );
}
