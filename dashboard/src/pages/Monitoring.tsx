import { Empty, ErrorLine, Kpi, Loading, Meter, Panel, Tag, ago } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { bytes, system } from "../lib/integrations";

function uptime(seconds?: number | null): string {
  if (!seconds) return "n/a";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return d > 0 ? `${d} j ${h} h` : `${h} h ${Math.floor((seconds % 3600) / 60)} min`;
}

export function Monitoring() {
  const [data, refresh] = useAsync(() => system.snapshot());
  const [events] = useAsync(() => system.events(20));

  if (data.state === "error") return <ErrorLine>{data.error}</ErrorLine>;
  if (data.state !== "ok") {
    return (
      <div className="stack">
        <div className="page-head">
          <h1>Monitoring &amp; KPI</h1>
        </div>
        <Panel title="Mesures">
          <Loading rows={4} />
        </Panel>
      </div>
    );
  }

  const { host, factory, gates, env } = data.data;
  const mem = host.memory;
  const disk = host.disk;
  const load = host.load_1m ?? 0;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Monitoring &amp; KPI</h1>
          <p className="sub">
            Chiffres lus dans <span className="mono">/proc</span>, <span className="mono">statvfs</span> et la base —
            aucun indicateur simulé. Ce que la plateforme ne mesure pas encore est affiché « n/a », pas estimé.
          </p>
        </div>
        <div className="head-right">
          <span className="chip">env {env}</span>
          <button className="btn ghost" onClick={refresh} type="button">
            Actualiser
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi icon="◎" label="Agents" value={String(factory.agents)} hint="enregistrés dans la base" />
        <Kpi
          icon="◈"
          label="Spécifications"
          value={String(factory.specifications)}
          hint={`${factory.specifications_approved} approuvées`}
        />
        <Kpi icon="▤" label="Événements d'audit" value={String(factory.audit_events)} hint="écrits en base" tone="flat" />
        <Kpi icon="◔" label="Charge 1 min" value={load.toFixed(2)} hint={`${host.cpu_count} vCPU · up ${uptime(host.uptime_seconds)}`} tone={load > host.cpu_count ? "warn" : "ok"} />
      </div>

      <div className="grid-2">
        <Panel title="Ressources de l'hôte">
          <Meter
            value={mem.used_percent ?? 0}
            label={`Mémoire ${mem.used_percent ?? "n/a"} %`}
            hint={`${bytes((mem.total_kb ?? 0) * 1024)} au total, ${bytes((mem.available_kb ?? 0) * 1024)} disponibles`}
            tone={(mem.used_percent ?? 0) > 85 ? "bad" : (mem.used_percent ?? 0) > 70 ? "warn" : undefined}
          />
          <Meter
            value={disk.used_percent ?? 0}
            label={`Disque ${disk.used_percent ?? "n/a"} %`}
            hint={`${bytes(disk.used_bytes)} utilisés sur ${bytes(disk.total_bytes)}`}
            tone={(disk.used_percent ?? 0) > 90 ? "bad" : (disk.used_percent ?? 0) > 80 ? "warn" : undefined}
          />
          <div className="kv">
            <span>Charge</span>
            <b>
              {host.load_1m?.toFixed(2) ?? "n/a"} / {host.load_5m?.toFixed(2) ?? "n/a"} /{" "}
              {host.load_15m?.toFixed(2) ?? "n/a"}
            </b>
          </div>
          <div className="kv">
            <span>Limite mémoire du conteneur</span>
            <b>{bytes(mem.cgroup_limit_bytes)}</b>
          </div>
        </Panel>

        <Panel title="Exécutions et portes">
          {Object.keys(factory.runs_by_status).length === 0 ? (
            <Empty>Aucune exécution enregistrée — les compteurs apparaîtront au premier workflow.</Empty>
          ) : (
            Object.entries(factory.runs_by_status).map(([status, n]) => (
              <div key={status} className="kv">
                <span>{status}</span>
                <b>{n}</b>
              </div>
            ))
          )}
          <div className="kv">
            <span>Approbation humaine</span>
            <b>{gates.human_approval_required ? "exigée" : "désactivée"}</b>
          </div>
          <div className="kv">
            <span>Écriture Jira</span>
            <Tag tone={gates.jira_write_enabled ? "warn" : "mut"}>
              {gates.jira_write_enabled ? "activée (projet SCRUM)" : "désactivée"}
            </Tag>
          </div>
          <div className="kv">
            <span>Déploiement prod</span>
            <b>{gates.deployment_requires_go_no_go ? "Go/No-Go obligatoire" : "non verrouillé"}</b>
          </div>
        </Panel>
      </div>

      <Panel title="Derniers événements" action={<Tag tone="mut">{events.state === "ok" ? events.data.count : "…"}</Tag>}>
        {events.state === "error" ? (
          <ErrorLine>{events.error}</ErrorLine>
        ) : events.state !== "ok" ? (
          <Loading rows={3} />
        ) : events.data.items.length === 0 ? (
          <Empty>Aucun événement pour le moment.</Empty>
        ) : (
          <table className="tbl">
            <thead>
              <tr>
                <th>Action</th>
                <th>Acteur</th>
                <th>Objet</th>
                <th>Quand</th>
              </tr>
            </thead>
            <tbody>
              {events.data.items.map((e, i) => (
                <tr key={e.id ?? i}>
                  <td className="mono">{e.action}</td>
                  <td>{e.actor}</td>
                  <td className="mono">
                    {e.entity_type ?? "—"}
                    {e.entity_id ? `/${String(e.entity_id).slice(0, 8)}` : ""}
                  </td>
                  <td className="mono">{e.created_at ? ago(e.created_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
