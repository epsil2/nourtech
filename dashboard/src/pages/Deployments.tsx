import { useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { actor } from "../lib/api";
import { cp } from "../lib/cp";
import { useAsync } from "../lib/useAsync";

const BLANK = { project_id: "", environment: "QA", version: "", commit_sha: "" };

export function Deployments() {
  const [metrics, refreshMetrics] = useAsync(() => cp.metrics());
  const [projects] = useAsync(() => cp.projects());
  const [envs] = useAsync(() => cp.environments());
  const [list, refreshList] = useAsync(() => cp.deployments());
  const [form, setForm] = useState({ ...BLANK });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = () => {
    refreshMetrics();
    refreshList();
  };

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const request = () =>
    run(async () => {
      const deployment = await cp.requestDeployment({
        project_id: form.project_id,
        environment: form.environment,
        version: form.version.trim(),
        commit_sha: form.commit_sha.trim(),
      });
      setNotice(
        deployment.status === "awaiting_approval"
          ? `${deployment.environment} : en attente de votre validation explicite.`
          : `${deployment.environment} : demande enregistrée.`,
      );
      setForm({ ...BLANK });
      refresh();
    });

  const confirm = (id: string, environment: string) =>
    run(async () => {
      const operator = window.prompt("Votre nom (opérateur qui valide) ?", actor());
      if (!operator) return;
      const evidence = window.prompt("Preuve (URL, sortie de test, identifiant de déploiement) ?", "") ?? "";
      await cp.confirmDeployment(id, {
        outcome: "deployed",
        actor: operator,
        evidence,
        go: true,
      });
      setNotice(`${environment} : déploiement confirmé par ${operator}.`);
      refresh();
    });

  const rollback = (id: string) =>
    run(async () => {
      const reason = window.prompt("Motif du retour arrière ?", "");
      if (!reason) return;
      await cp.rollback(id, reason);
      setNotice("Retour arrière enregistré — l'exécution reste à la charge de la chaîne de release.");
      refresh();
    });

  const deployments = list.state === "ok" ? list.data.deployments : [];
  const projectList = projects.state === "ok" ? projects.data.projects : [];
  const environmentList = envs.state === "ok" ? envs.data.environments : [];
  const m = metrics.state === "ok" ? metrics.data : null;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Déploiements</h1>
          <p className="sub">
            La chaîne reste DEV → QA → tag humain → watcher VPS → PROD. Camilia enregistre la demande, impose la
            validation humaine sur PRE-PROD et PROD, et ne marque un déploiement réussi qu'avec une preuve.
          </p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={refresh} type="button">
            Actualiser
          </button>
        </div>
      </div>

      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <div className="tag t-info">{notice}</div>}
      {metrics.state === "loading" && <Loading rows={2} />}
      {metrics.state === "error" && <ErrorLine>{metrics.error}</ErrorLine>}

      {m && (
        <div className="cards">
          <Kpi icon="◇" label="Tickets Jira" value={String(m.issues.total)} hint="rattachés aux projets" />
          <Kpi
            icon="✓"
            label="Taux de réussite"
            value={m.success_rate === null ? "n/a" : `${Math.round(m.success_rate * 100)} %`}
            hint={m.success_rate === null ? "aucune exécution terminée" : "exécutions terminées"}
            tone={m.success_rate === null ? "flat" : "ok"}
          />
          <Kpi
            icon="⏱"
            label="Durée moyenne"
            value={m.mean_duration_seconds === null ? "n/a" : `${Math.round(m.mean_duration_seconds / 60)} min`}
            hint="début → fin des runs"
            tone="flat"
          />
          <Kpi icon="◈" label="Déploiements" value={String(m.deployments.total)} hint="demandes enregistrées" tone="flat" />
        </div>
      )}
      {m && (
        <p className="sub">
          coût : <b>n/a</b> — {m.cost_note}. Indicateurs générés le {new Date(m.generated_at).toLocaleString("fr-FR")}.
        </p>
      )}

      <div className="grid-2">
        <Panel title="Demandes de déploiement">
          {list.state === "loading" && <Loading rows={3} />}
          {list.state === "ok" && deployments.length === 0 && (
            <Empty>Aucune demande. Un déploiement sans preuve n'est jamais marqué comme réussi.</Empty>
          )}
          {deployments.length > 0 && (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Env.</th>
                  <th>Version</th>
                  <th>Commit</th>
                  <th>Statut</th>
                  <th>Demandé</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {deployments.map((deployment) => (
                  <tr key={deployment.id}>
                    <td>
                      <Tag tone={deployment.environment === "PROD" ? "warn" : "mut"}>{deployment.environment}</Tag>
                    </td>
                    <td className="mono">{deployment.version}</td>
                    <td className="mono">{(deployment.commit_sha || "—").slice(0, 7)}</td>
                    <td>
                      <Tag tone={statusTone(deployment.status)}>{deployment.status}</Tag>
                    </td>
                    <td>{ago(deployment.created_at)}</td>
                    <td>
                      {["awaiting_approval", "requested"].includes(deployment.status) && (
                        <button
                          className="btn sm"
                          onClick={() => confirm(deployment.id, deployment.environment)}
                          disabled={busy}
                          type="button"
                        >
                          Confirmer
                        </button>
                      )}
                      {deployment.status === "deployed" && (
                        <button className="btn ghost sm" onClick={() => rollback(deployment.id)} disabled={busy} type="button">
                          Retour arrière
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Nouvelle demande">
          <div className="field">
            <span>Projet</span>
            <select value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>
              <option value="">— choisir —</option>
              {projectList.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.key} · {project.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <span>Environnement</span>
            <select value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
              {environmentList.map((env) => (
                <option key={env.id} value={env.name}>
                  {env.name}
                  {env.gate ? " (validation requise)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <span>Version</span>
            <input value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder="v1.2.3" />
          </div>
          <div className="field">
            <span>Commit</span>
            <input
              value={form.commit_sha}
              onChange={(e) => setForm({ ...form, commit_sha: e.target.value })}
              placeholder="abc1234"
            />
          </div>
          <button className="btn" onClick={request} disabled={busy || !form.project_id || form.version.length < 1} type="button">
            Enregistrer la demande
          </button>
          <p className="sub">
            Camilia n'exécute pas le déploiement : la publication reste faite par le pipeline et le watcher existants,
            puis confirmée ici avec sa preuve.
          </p>
        </Panel>
      </div>
    </div>
  );
}
