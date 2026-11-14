import { useAsync } from "../lib/useAsync";
import { api, listOf } from "../lib/api";
import type { Agent, AuditRow, Evaluation, JiraStatus, Metrics, Release, Resources, Workflow, WorkflowDefinition } from "../lib/api";
import { Empty, ErrorLine, Kpi, Loading, Meter, Panel, Tag, ago, statusTone } from "../components/ui";

export function Overview({ onNewAgent }: { onNewAgent: () => void }) {
  const [metrics] = useAsync(() => api.get<Metrics>("/api/v1/metrics"));
  const [resources] = useAsync(() => api.get<Resources>("/api/v1/observability/resources"));
  const [workflows] = useAsync(() => api.get<{ items: Workflow[] }>("/api/v1/workflows?limit=100"));
  const [definitions] = useAsync(() => api.get<{ items: WorkflowDefinition[] }>("/api/v1/workflows/definitions"));
  const [jira] = useAsync(() => api.get<JiraStatus>("/api/v1/integrations/jira/status"));
  const [agents] = useAsync(() => api.get<{ items: Agent[] }>("/api/v1/agents"));
  const [evaluations] = useAsync(() => api.get<{ items: Evaluation[] }>("/api/v1/evaluations?limit=50"));
  const [releases] = useAsync(() => api.get<{ items: Release[] }>("/api/v1/releases"));
  const [audit] = useAsync(() => api.get<{ items: AuditRow[] }>("/api/v1/audit?limit=12"));

  const rows = listOf(workflows.state === "ok" ? workflows.data : null);
  const waiting = rows.filter((w) => w.status === "awaiting_approval");
  const evals = listOf(evaluations.state === "ok" ? evaluations.data : null);
  const passed = evals.filter((e) => e.passed).length;
  const rels = listOf(releases.state === "ok" ? releases.data : null);
  const agentList = listOf(agents.state === "ok" ? agents.data : null);
  const events = listOf(audit.state === "ok" ? audit.data : null);
  const allDefinitions = listOf(definitions.state === "ok" ? definitions.data : null);

  const mem = resources.state === "ok" ? resources.data.memory_mib : null;
  const memUsedPct = mem ? ((mem.MemTotal - mem.MemAvailable) / mem.MemTotal) * 100 : 0;
  const diskPct = resources.state === "ok" ? resources.data.disk.used_pct : 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Command Center</h1>
          <div className="sub">
            Créer, approuver, livrer et surveiller vos agents. Chaque chiffre vient de l'API AI Factory —
            aucun indicateur n'est simulé.
          </div>
        </div>
        <div className="head-right">
          <span className="chip">API {metrics.state === "ok" ? `${metrics.data.store}` : "…"}</span>
          <button className="btn" onClick={onNewAgent}>
            Nouvel agent <span style={{ opacity: 0.8 }}>→</span>
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi
          icon="◎"
          label="Agents"
          value={agents.state === "error" ? "n/a" : String(agentList.length)}
          hint={agentList.length ? "registre actif" : "aucun agent — le wizard crée le premier"}
          tone={agentList.length ? "ok" : "flat"}
        />
        <Kpi
          icon="◈"
          label="Workflows"
          value={workflows.state === "ok" ? String(rows.length) : "…"}
          hint={`${waiting.length} en attente de votre décision`}
          tone={waiting.length ? "warn" : "flat"}
        />
        <Kpi
          icon="✓"
          label="Qualité"
          value={evals.length ? `${Math.round((passed / evals.length) * 100)} %` : "n/a"}
          hint={evals.length ? `${passed}/${evals.length} évaluations passées` : "aucune évaluation enregistrée"}
          tone={evals.length && passed < evals.length ? "warn" : "flat"}
        />
        <Kpi
          icon="⇪"
          label="Releases"
          value={releases.state === "ok" ? String(rels.length) : "…"}
          hint={rels.length ? "chaîne release" : "aucune release publiée"}
          tone="flat"
        />
      </div>

      <div className="grid-2">
        <div className="stack">
          <Panel
            title="Chaîne de fabrication"
            action={<span className="pill">définitions servies par l'API</span>}
          >
            {definitions.state === "loading" && <Loading rows={4} />}
            {definitions.state === "error" && <ErrorLine>{definitions.error}</ErrorLine>}
            {allDefinitions.map((def) => (
              <div key={def.name} style={{ marginBottom: 14 }}>
                <div className="mini" style={{ marginTop: 0, marginBottom: 6 }}>
                  <b style={{ textTransform: "capitalize" }}>{def.name}</b>
                  <span>{def.nodes.filter((n) => n.approval).length} porte(s) humaine(s)</span>
                </div>
                <div className="chain">
                  {def.nodes.map((node, i) => (
                    <div key={node.name}>
                      <div className={`chain-step ${node.approval ? "gate" : ""}`}>
                        <span className="idx">{i + 1}</span>
                        <div style={{ minWidth: 0 }}>
                          <div className="nm">{node.name}</div>
                          <div className="lb">{node.label}</div>
                        </div>
                        <span style={{ marginLeft: "auto" }}>
                          {node.approval ? <Tag tone="warn">approbation</Tag> : <Tag tone="mut">auto</Tag>}
                        </span>
                      </div>
                      {i < def.nodes.length - 1 && <div className="chain-arrow" />}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </Panel>

          <Panel title="File d'approbation" action={<span className="link">ce qui attend une décision</span>}>
            {workflows.state === "loading" && <Loading />}
            {workflows.state === "error" && <ErrorLine>{workflows.error}</ErrorLine>}
            {workflows.state === "ok" && waiting.length === 0 && (
              <Empty>Rien en attente : aucun workflow n'est arrêté sur une porte d'approbation.</Empty>
            )}
            {waiting.map((w) => (
              <div className="row-item" key={w.id}>
                <span className="ico">◈</span>
                <div className="t">
                  <b>
                    {w.definition} · {w.current_step}
                  </b>
                  <span>
                    {w.id.slice(0, 8)} · étape {w.current_step}
                  </span>
                </div>
                <Tag tone="warn">à valider</Tag>
              </div>
            ))}
          </Panel>
        </div>

        <div className="stack">
          <Panel title="Système" action={<span className="link">{resources.state === "ok" ? `${resources.data.cpu_count} vCPU` : ""}</span>}>
            {resources.state === "loading" && <Loading rows={3} />}
            {resources.state === "error" && <ErrorLine>{resources.error}</ErrorLine>}
            {resources.state === "ok" && (
              <>
                <Meter
                  value={memUsedPct}
                  label="Mémoire"
                  hint={`${Math.round((mem!.MemTotal - mem!.MemAvailable) / 1024 * 10) / 10} / ${Math.round(mem!.MemTotal / 1024)} Go`}
                  tone={memUsedPct > 85 ? "bad" : memUsedPct > 70 ? "warn" : undefined}
                />
                <div style={{ height: 10 }} />
                <Meter
                  value={diskPct}
                  label="Disque"
                  hint={`${Math.round(resources.data.disk.used_pct)} % — ${Math.round(resources.data.disk.total_mib / 1024)} Go`}
                  tone={diskPct > 85 ? "bad" : diskPct > 70 ? "warn" : undefined}
                />
                <div style={{ height: 10 }} />
                <div className="kv">
                  <span>Uptime API</span>
                  <b>{Math.floor(resources.data.uptime_s / 3600)} h {Math.floor((resources.data.uptime_s % 3600) / 60)} min</b>
                </div>
                <div className="kv">
                  <span>Événements d'audit</span>
                  <b>{resources.data.audit_events}</b>
                </div>
                <div className="kv">
                  <span>Artefacts / releases</span>
                  <b>
                    {resources.data.artifacts} / {resources.data.releases}
                  </b>
                </div>
              </>
            )}
          </Panel>

          <Panel title="Intégrations">
            {jira.state === "loading" && <Loading rows={2} />}
            {jira.state === "error" && <ErrorLine>{jira.error}</ErrorLine>}
            {jira.state === "ok" && (
              <>
                <div className="row-item">
                  <span className="ico">J</span>
                  <div className="t">
                    <b>Jira — {jira.data.site ?? "non configuré"}</b>
                    <span>
                      {jira.data.configured ? "connecté" : "absent"} · écriture{" "}
                      {jira.data.write_enabled ? `ouverte (${jira.data.write_projects?.join(", ")})` : "fermée"}
                    </span>
                  </div>
                  <Tag tone={jira.data.configured ? (jira.data.write_enabled ? "ok" : "warn") : "bad"}>
                    {jira.data.configured ? (jira.data.write_enabled ? "lecture+écriture" : "lecture") : "off"}
                  </Tag>
                </div>
                <div className="row-item">
                  <span className="ico">G</span>
                  <div className="t">
                    <b>GitHub — epsil2</b>
                    <span>dépôts privés · PR par story approuvée</span>
                  </div>
                  <Tag tone="info">prêt</Tag>
                </div>
              </>
            )}
          </Panel>

          <Panel title="Activité récente">
            {audit.state === "loading" && <Loading />}
            {audit.state === "error" && <Empty>Journal indisponible : {audit.error}</Empty>}
            {audit.state === "ok" && events.length === 0 && <Empty>Aucun événement.</Empty>}
            {events.map((e, i) => (
              <div className="row-item" key={`${e.id ?? i}`}>
                <span className="ico">·</span>
                <div className="t">
                  <b>{e.action}</b>
                  <span>
                    {e.actor ?? "—"} · {ago(e.at)}
                  </span>
                </div>
                <Tag tone={statusTone(e.action.includes("fail") ? "failed" : "ok")}>
                  {e.entity_type ?? "—"}
                </Tag>
              </div>
            ))}
          </Panel>
        </div>
      </div>
    </>
  );
}
