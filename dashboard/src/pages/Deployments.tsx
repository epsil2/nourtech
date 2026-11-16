import { useMemo, useState } from "react";
import { Empty, ErrorLine, Kpi, Loading, Panel, Tag, ago, statusTone } from "../components/ui";
import { actor } from "../lib/api";
import { cp } from "../lib/cp";
import { board } from "../lib/integrations";
import type { BoardFacetSprint } from "../lib/integrations";
import { useAsync } from "../lib/useAsync";

const BLANK = { project_id: "", environment: "QA", version: "", commit_sha: "", sprint_id: "", sprint_name: "" };

function sprintVersion(name: string | null, date = new Date()): string {
  const stamp = `${String(date.getMonth() + 1).padStart(2, "0")}${String(date.getDate()).padStart(2, "0")}`;
  const digits = (name ?? "").match(/\d+/);
  return digits ? `v${digits[0]}.${stamp}` : `v${date.getFullYear()}.${stamp}`;
}

function shorten(text: string, max = 34): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function Deployments() {
  const [metrics, refreshMetrics] = useAsync(() => cp.metrics());
  const [projects] = useAsync(() => cp.deploymentProjects());
  const [envs] = useAsync(() => cp.environments());
  const [list, refreshList] = useAsync(() => cp.deployments());
  const [form, setForm] = useState({ ...BLANK });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const projectList = projects.state === "ok" ? projects.data.projects : [];
  const current = projectList.find((project) => project.id === form.project_id);
  const jiraProject = current?.jira_project ?? current?.key ?? "SCRUM";

  const [sprints] = useAsync(() => board.sprints(jiraProject, {}), [jiraProject]);
  const sprintChoices: BoardFacetSprint[] = sprints.state === "ok" ? sprints.data.facets.sprints : [];

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

  const pickProject = (id: string) => {
    const project = projectList.find((item) => item.id === id);
    setForm((prev) => ({
      ...prev,
      project_id: id,
      sprint_id: "",
      sprint_name: "",
      version: prev.version || sprintVersion(project?.key ?? null),
    }));
  };

  const pickSprint = (id: string) => {
    const sprint = sprintChoices.find((item) => String(item.id) === id);
    setForm((prev) => ({
      ...prev,
      sprint_id: id,
      sprint_name: sprint?.name ?? "",
      // The version tag is a suggestion, not a rule: it stays editable.
      version: sprint ? sprintVersion(sprint.name) : prev.version,
    }));
  };

  const request = () =>
    run(async () => {
      const deployment = await cp.requestDeployment({
        project_id: form.project_id,
        environment: form.environment,
        version: form.version.trim(),
        commit_sha: form.commit_sha.trim(),
        sprint_id: form.sprint_id,
        sprint_name: form.sprint_name,
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
      await cp.confirmDeployment(id, { outcome: "deployed", actor: operator, evidence, go: true });
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
  const environmentList = envs.state === "ok" ? envs.data.environments : [];
  const m = metrics.state === "ok" ? metrics.data : null;

  const commitHint = useMemo(
    () => (form.commit_sha.trim() ? `sha ${form.commit_sha.trim().slice(0, 7)}` : "vide → HEAD publié par le pipeline"),
    [form.commit_sha],
  );

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
            <span className="pict">⟳</span> Actualiser
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
                  <th>Sprint</th>
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
                    <td className="mono">{deployment.sprint_name || "—"}</td>
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
                          <span className="pict">✓</span> Confirmer
                        </button>
                      )}
                      {deployment.status === "deployed" && (
                        <button className="btn ghost sm" onClick={() => rollback(deployment.id)} disabled={busy} type="button">
                          <span className="pict">↺</span> Retour arrière
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
          <label className="field">
            <span>Projet</span>
            <select className="inp" value={form.project_id} onChange={(e) => pickProject(e.target.value)}>
              <option value="">— choisir —</option>
              {projectList.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.key} · {project.name}
                </option>
              ))}
            </select>
            {projects.state === "ok" && projectList.length === 0 && (
              <small>Aucun projet : créez-en un dans Project Factory ou rattachez un projet Jira.</small>
            )}
          </label>

          <label className="field">
            <span>Sprint à déployer</span>
            <select className="inp" value={form.sprint_id} onChange={(e) => pickSprint(e.target.value)} disabled={!form.project_id}>
              <option value="">Hors sprint</option>
              {sprintChoices.map((sprint) => (
                <option key={sprint.id} value={String(sprint.id)}>
                  {shorten(sprint.name, 32)} · {sprint.issues} ticket(s)
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Environnement</span>
            <select className="inp" value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
              {environmentList.map((env) => (
                <option key={env.id} value={env.name}>
                  {env.name}
                  {env.gate ? " (validation requise)" : ""}
                </option>
              ))}
            </select>
          </label>

          <div className="grid-2">
            <label className="field">
              <span>Version (suggérée)</span>
              <input className="inp" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder="v3.1116" />
              <small>proposée depuis le sprint ; restez libre de la corriger</small>
            </label>
            <label className="field">
              <span>Commit</span>
              <input
                className="inp"
                value={form.commit_sha}
                onChange={(e) => setForm({ ...form, commit_sha: e.target.value })}
                placeholder="abc1234"
              />
              <small>{commitHint}</small>
            </label>
          </div>

          <button
            className="btn"
            onClick={request}
            disabled={busy || !form.project_id || form.version.length < 1}
            type="button"
            style={{ width: "100%", justifyContent: "center" }}
          >
            <span className="pict">↑</span> Enregistrer la demande
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
