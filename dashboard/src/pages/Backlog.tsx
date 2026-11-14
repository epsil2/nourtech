import { useMemo, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { board } from "../lib/integrations";
import type { BoardIssue } from "../lib/integrations";

const PROJECT = "SCRUM";

export function Backlog() {
  const [data, refresh] = useAsync(() => board.backlog(PROJECT));
  const [needle, setNeedle] = useState("");
  const [busy, setBusy] = useState(false);

  const items: BoardIssue[] = data.state === "ok" ? data.data.items : [];

  const filtered = useMemo(() => {
    const q = needle.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (i) =>
        i.key.toLowerCase().includes(q) ||
        i.summary.toLowerCase().includes(q) ||
        (i.status ?? "").toLowerCase().includes(q) ||
        (i.assignee ?? "").toLowerCase().includes(q),
    );
  }, [items, needle]);

  const reload = () => {
    setBusy(true);
    refresh();
    setTimeout(() => setBusy(false), 800);
  };

  if (data.state === "error") {
    return (
      <ErrorLine>
        {data.error}
        {data.error.includes("503") || data.error.toLowerCase().includes("jira")
          ? " — vérifiez que JIRA_SITE_URL, JIRA_ACCOUNT_EMAIL et JIRA_API_TOKEN sont présents dans l'environnement du service."
          : ""}
      </ErrorLine>
    );
  }

  const counts = data.state === "ok" ? data.data.by_status : {};

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Backlog Jira</h1>
          <p className="sub">
            Projet <b>{PROJECT}</b>, lu en direct depuis Jira. Cette page ne crée, ne déplace et ne ferme rien :
            la création de tickets reste soumise à votre approbation du backlog.
          </p>
        </div>
        <div className="head-right">
          <input
            className="inp"
            placeholder="filtrer (clé, résumé, statut, assigné)"
            value={needle}
            onChange={(e) => setNeedle(e.target.value)}
          />
          <button className="btn" onClick={reload} disabled={busy || data.state === "loading"} type="button">
            {busy || data.state === "loading" ? "Actualisation…" : "Actualiser"}
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi icon="▤" label="Tickets" value={data.state === "ok" ? String(data.data.count) : "…"} hint={`projet ${PROJECT}`} />
        <Kpi
          icon="◔"
          label="Restant"
          value={
            data.state === "ok"
              ? String(Object.entries(counts).filter(([s]) => s !== "Done").reduce((n, [, v]) => n + v, 0))
              : "…"
          }
          hint="tout ce qui n'est pas Done"
        />
        <Kpi
          icon="✎"
          label="Filtre"
          value={String(filtered.length)}
          hint={needle ? "tickets affichés" : "aucun filtre"}
          tone="flat"
        />
      </div>

      <Panel
        title="Tickets"
        action={
          <div className="chips">
            {Object.entries(counts).map(([status, n]) => (
              <Tag key={status} tone={statusTone(status)}>
                {status} {n}
              </Tag>
            ))}
          </div>
        }
      >
        {data.state === "loading" ? (
          <Loading rows={5} />
        ) : filtered.length === 0 ? (
          <Empty>Aucun ticket ne correspond.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Clé</th>
                <th>Type</th>
                <th>Résumé</th>
                <th>Statut</th>
                <th>Assigné</th>
                <th>Sprint</th>
                <th>Maj</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((i) => (
                <tr key={i.key}>
                  <td className="mono">
                    {i.url ? (
                      <a href={i.url} target="_blank" rel="noreferrer">
                        {i.key}
                      </a>
                    ) : (
                      i.key
                    )}
                  </td>
                  <td>{i.type ?? "—"}</td>
                  <td>{i.summary}</td>
                  <td>
                    <Tag tone={statusTone(i.status ?? "")}>{i.status ?? "—"}</Tag>
                  </td>
                  <td>{i.assignee ?? "non assigné"}</td>
                  <td>{i.sprints.length ? i.sprints.map((s) => s.name).join(", ") : "backlog"}</td>
                  <td className="mono">{i.updated ? ago(i.updated) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
