import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { gh } from "../lib/integrations";

export function GitHub() {
  const [status] = useAsync(() => gh.status());
  const [data, refresh] = useAsync(() => gh.overview());

  if (status.state === "ok" && !status.data.configured) {
    return (
      <div className="stack">
        <div className="page-head">
          <h1>GitHub</h1>
        </div>
        <ErrorLine>
          Aucun jeton GitHub dans l'environnement du service (<span className="mono">GITHUB_TOKEN</span>,{" "}
          <span className="mono">GITHUB_REPO</span>). La page restera vide tant que ces deux variables ne sont pas
          fournies au conteneur — elles ne doivent jamais être exposées au navigateur.
        </ErrorLine>
      </div>
    );
  }

  if (data.state === "error") return <ErrorLine>{data.error}</ErrorLine>;

  if (data.state === "loading") {
    return (
      <div className="stack">
        <div className="page-head">
          <h1>GitHub</h1>
        </div>
        <Panel title="Dépôt">
          <Loading rows={5} />
        </Panel>
      </div>
    );
  }

  const { repo, pulls, commits, actions, releases } = data.data;
  const openPulls = pulls.filter((p) => p.state === "open").length;
  const lastRun = actions[0];

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>GitHub</h1>
          <p className="sub">
            <a href={repo.html_url} target="_blank" rel="noreferrer">
              {repo.full_name}
            </a>{" "}
            · branche par défaut <span className="mono">{repo.default_branch}</span> ·{" "}
            {repo.private ? "privé" : "public"} · accès contrat <b>lecture seule</b>
            {repo.permissions?.push ? " (le jeton porte pourtant push : à restreindre)" : ""}
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={refresh} type="button">
            Actualiser
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi icon="⑂" label="Pull requests" value={String(openPulls)} hint={`${pulls.length} récentes`} />
        <Kpi
          icon="✓"
          label="Dernier workflow"
          value={lastRun ? lastRun.conclusion ?? lastRun.status : "n/a"}
          hint={lastRun ? `${lastRun.name} · ${lastRun.branch ?? "—"}` : "aucun run"}
          tone={lastRun && lastRun.conclusion && lastRun.conclusion !== "success" ? "warn" : "ok"}
        />
        <Kpi icon="◆" label="Releases" value={String(releases.length)} hint="tags publiés" />
      </div>

      <div className="grid-2">
        <Panel title="Pull requests" action={<Tag tone="mut">{pulls.length}</Tag>}>
          {pulls.length === 0 ? (
            <Empty>Aucune pull request.</Empty>
          ) : (
            pulls.map((p) => (
              <div key={p.number} className="row-item">
                <b className="mono">#{p.number}</b>
                <span className="t">
                  <a href={p.html_url} target="_blank" rel="noreferrer">
                    {p.title}
                  </a>
                </span>
                <Tag tone={p.merged ? "ok" : p.state === "open" ? "info" : "mut"}>
                  {p.merged ? "mergée" : p.state}
                </Tag>
              </div>
            ))
          )}
        </Panel>

        <Panel title="Derniers commits">
          {commits.length === 0 ? (
            <Empty>Aucun commit.</Empty>
          ) : (
            commits.map((c) => (
              <div key={c.sha} className="row-item">
                <b className="mono">{c.sha}</b>
                <span className="t">
                  <b>{c.message}</b>
                  <span>
                    {c.author ?? "—"} · {ago(c.date)}
                  </span>
                </span>
              </div>
            ))
          )}
        </Panel>
      </div>

      <Panel title="Workflows GitHub Actions" action={<Tag tone="mut">{actions.length} derniers</Tag>}>
        {actions.length === 0 ? (
          <Empty>Aucun workflow exécuté.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Workflow</th>
                <th>Événement</th>
                <th>Branche</th>
                <th>Résultat</th>
                <th>Quand</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((r, idx) => (
                <tr key={`${r.name}-${idx}`}>
                  <td>
                    <a href={r.html_url} target="_blank" rel="noreferrer">
                      {r.name}
                    </a>
                  </td>
                  <td>{r.event ?? "—"}</td>
                  <td className="mono">{r.branch ?? "—"}</td>
                  <td>
                    <Tag tone={r.conclusion === "success" ? "ok" : r.conclusion ? "bad" : "info"}>
                      {r.conclusion ?? r.status}
                    </Tag>
                  </td>
                  <td className="mono">{ago(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
