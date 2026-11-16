import { useEffect, useState } from "react";
import { Sidebar, NAV } from "./components/Sidebar";
import { ErrorBoundary } from "./components/ErrorBoundary";
import type { PageKey } from "./components/Sidebar";
import { Agents } from "./pages/Agents";
import { Deployments } from "./pages/Deployments";
import { Executions } from "./pages/Executions";
import { Factory } from "./pages/Factory";
import { Login } from "./pages/Login";
import { Backlog } from "./pages/Backlog";
import { GitHub } from "./pages/GitHub";
import { Integrations } from "./pages/Integrations";
import { Monitoring } from "./pages/Monitoring";
import { Overview } from "./pages/Overview";
import { Portfolio } from "./pages/Portfolio";
import { Releases } from "./pages/Releases";
import { Soon } from "./pages/Soon";
import { Sprints } from "./pages/Sprints";
import { api } from "./lib/api";
import { logout, me } from "./lib/session";
import type { User } from "./lib/session";
import { useTheme } from "./lib/theme";

function readHash(): PageKey {
  const key = window.location.hash.replace("#", "") as PageKey;
  return NAV.some((n) => n.key === key) ? key : "overview";
}

function TopBar({ go, username, role }: { go: (key: PageKey) => void; username?: string; role?: string }) {
  const [theme, setTheme] = useTheme();
  const [query, setQuery] = useState("");
  const hits = query.trim()
    ? NAV.filter((n) => n.label.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 6)
    : [];

  return (
    <div className="topbar">
      <div className="search">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M16.5 16.5 21 21" />
        </svg>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits[0]) {
              go(hits[0].key);
              setQuery("");
            }
            if (e.key === "Escape") setQuery("");
          }}
          placeholder="Rechercher une section…"
          aria-label="Rechercher une section"
        />
        {hits.length > 0 && (
          <div className="search__hits">
            {hits.map((hit) => (
              <button
                key={hit.key}
                type="button"
                onClick={() => {
                  go(hit.key);
                  setQuery("");
                }}
              >
                {hit.label}
                <span>#{hit.key}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="topbar__spacer" />

      <div className="seg" role="group" aria-label="Thème">
        <button type="button" className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19" />
          </svg>
          Light
        </button>
        <button type="button" className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" />
          </svg>
          Dark
        </button>
      </div>

      {username && (
        <div className="who">
          <div className="avatar">{username.slice(0, 2).toUpperCase()}</div>
          <div style={{ lineHeight: 1.25 }}>
            <div style={{ color: "var(--txt)", fontWeight: 600 }}>{username}</div>
            <div style={{ fontSize: 10 }}>{role ?? "member"}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<PageKey>(readHash);
  const [health, setHealth] = useState<{ ok: boolean | null; label: string }>({ ok: null, label: "connexion à l'API…" });
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    me()
      .then((resolved) => alive && setUser(resolved))
      .catch(() => alive && setUser(null))
      .finally(() => alive && setChecking(false));
    const onUnauthorised = () => setUser(null);
    window.addEventListener("camilia:unauthorised", onUnauthorised);
    return () => {
      alive = false;
      window.removeEventListener("camilia:unauthorised", onUnauthorised);
    };
  }, []);

  const signOut = async () => {
    try {
      await logout();
    } finally {
      setUser(null);
    }
  };

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
        .then(
          (h) =>
            alive &&
            setHealth({
              ok: true,
              label: `${h.status ?? "ok"} · env ${h.env ?? "—"} · store ${h.store === "memory" ? "memory (volatile)" : "PostgreSQL"}`,
            }),
        )
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

  if (checking) {
    return (
      <div className="shell shell--auth">
        <main className="auth">
          <p className="muted">Vérification de la session…</p>
        </main>
      </div>
    );
  }

  if (!user) {
    return <Login onSignedIn={() => me().then(setUser).catch(() => setUser(null))} />;
  }

  return (
    <div className="shell">
      <Sidebar
        current={page}
        onNavigate={go}
        apiOk={health.ok}
        apiLabel={health.label}
        username={user.username}
        role={user.role}
        onSignOut={signOut}
      />
      <div className="body">
        <TopBar go={go} username={user.username} role={user.role} />
        <main>
          <ErrorBoundary page={page}>
          {page === "overview" && <Overview onNewAgent={() => go("agents")} />}
          {page === "portfolio" && <Portfolio onOpenFactory={() => go("factory")} />}
          {page === "factory" && <Factory />}
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
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
