import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { api, listOf } from "../lib/api";
import type { Evaluation, Release } from "../lib/api";
import { day, gh } from "../lib/integrations";

const STAGES = ["DEV", "QA", "PRE-PROD", "PROD"];

export function Releases() {
  const [runs] = useAsync(() => gh.actions());
  const [ghReleases] = useAsync(() => gh.releases());
  const [internal] = useAsync(() => api.get<{ items: Release[] }>("/api/v1/releases"));
  const [evals] = useAsync(() => api.get<{ items: Evaluation[] }>("/api/v1/evaluations?limit=20"));

  const internalRows = listOf(internal.state === "ok" ? internal.data : null);
  const evalRows = listOf(evals.state === "ok" ? evals.data : null);
  const passed = evalRows.filter((e) => e.passed).length;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>CI/CD &amp; Releases</h1>
          <p className="sub">
            Ce qui s'est réellement exécuté : workflows GitHub Actions, releases publiées, releases internes de la
            plateforme et évaluations. Rien n'est promu en production sans votre Go/No-Go explicite.
          </p>
        </div>
      </div>

      <Panel title="Chaîne de promotion">
        <div className="chips" style={{ gap: 10 }}>
          {STAGES.map((s, i) => (
            <span key={s} className={`step-pill ${s === "PROD" ? "on" : ""}`}>
              <span>{i + 1}</span>
              {s}
            </span>
          ))}
        </div>
        <div className="kv" style={{ marginTop: 10 }}>
          <span>Porte finale</span>
          <b>Go/No-Go humain avant PROD — jamais automatique</b>
        </div>
        <div className="kv">
          <span>État des environnements</span>
          <b>non instrumenté : aucun environnement QA/PRE-PROD n'existe encore sur ce VPS</b>
        </div>
      </Panel>

      <div className="cards">
        <Kpi
          icon="✓"
          label="Workflows"
          value={runs.state === "ok" ? String(runs.data.count) : "…"}
          hint="10 derniers runs GitHub"
        />
        <Kpi
          icon="◆"
          label="Releases GitHub"
          value={ghReleases.state === "ok" ? String(ghReleases.data.count) : "…"}
          hint="tags publiés sur le dépôt"
        />
        <Kpi
          icon="◈"
          label="Évaluations"
          value={evalRows.length ? `${passed}/${evalRows.length}` : "n/a"}
          hint={evalRows.length ? "évaluations passées" : "aucune évaluation"}
          tone={evalRows.length && passed < evalRows.length ? "warn" : "flat"}
        />
      </div>

      <Panel title="GitHub Actions" action={<Tag tone="mut">{runs.state === "ok" ? runs.data.count : "…"}</Tag>}>
        {runs.state === "error" ? (
          <ErrorLine>{runs.error}</ErrorLine>
        ) : runs.state !== "ok" ? (
          <Loading rows={3} />
        ) : runs.data.items.length === 0 ? (
          <Empty>Aucun run.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Workflow</th>
                <th>Branche</th>
                <th>Résultat</th>
                <th>Quand</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((r, i) => (
                <tr key={i}>
                  <td>{r.name}</td>
                  <td className="mono">{r.branch ?? "—"}</td>
                  <td>
                    <Tag tone={r.conclusion === "success" ? "ok" : r.conclusion ? "bad" : "info"}>
                      {r.conclusion ?? r.status}
                    </Tag>
                  </td>
                  <td className="mono">{ago(r.created_at)}</td>
                  <td>
                    <a href={r.html_url} target="_blank" rel="noreferrer">
                      voir
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Releases GitHub">
          {ghReleases.state !== "ok" ? (
            ghReleases.state === "error" ? (
              <ErrorLine>{ghReleases.error}</ErrorLine>
            ) : (
              <Loading rows={2} />
            )
          ) : ghReleases.data.items.length === 0 ? (
            <Empty>Aucune release publiée — les tags n'existent pas encore.</Empty>
          ) : (
            ghReleases.data.items.map((r) => (
              <div key={r.tag} className="row-item">
                <b className="mono">{r.tag}</b>
                <span className="t">{r.name}</span>
                <Tag tone={r.prerelease ? "warn" : "ok"}>{day(r.published_at)}</Tag>
              </div>
            ))
          )}
        </Panel>

        <Panel title="Releases internes (AI Factory)">
          {internal.state !== "ok" ? (
            internal.state === "error" ? (
              <ErrorLine>{internal.error}</ErrorLine>
            ) : (
              <Loading rows={2} />
            )
          ) : internalRows.length === 0 ? (
            <Empty>Aucune release enregistrée par la plateforme.</Empty>
          ) : (
            internalRows.map((r) => (
              <div key={r.id} className="row-item">
                <b className="mono">{r.version ?? r.id.slice(0, 8)}</b>
                <span className="t">
                  <b>{r.target ?? "—"}</b>
                  <span className="mono">{r.commit_sha?.slice(0, 7) ?? "—"}</span>
                </span>
                <Tag tone={r.status === "released" ? "ok" : "info"}>{r.status}</Tag>
              </div>
            ))
          )}
        </Panel>
      </div>
    </div>
  );
}
