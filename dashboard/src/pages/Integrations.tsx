import { ErrorLine, Kpi, Loading, Panel, Tag } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { integrations } from "../lib/integrations";

const CATEGORY_LABEL: Record<string, string> = {
  pilotage: "Pilotage",
  code: "Code",
  llm: "Modèles",
  observabilité: "Observabilité",
  notification: "Notification",
  documentation: "Documentation",
};

export function Integrations() {
  const [data, refresh] = useAsync(() => integrations.list());

  if (data.state === "error") return <ErrorLine>{data.error}</ErrorLine>;
  if (data.state !== "ok") {
    return (
      <div className="stack">
        <div className="page-head">
          <h1>Intégrations &amp; réglages</h1>
        </div>
        <Panel title="Fournisseurs">
          <Loading rows={5} />
        </Panel>
      </div>
    );
  }

  const { items, configured, count, suggestions, note } = data.data;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Intégrations &amp; réglages</h1>
          <p className="sub">{note}</p>
        </div>
        <div className="head-right">
          <button className="btn ghost" onClick={refresh} type="button">
            Vérifier
          </button>
        </div>
      </div>

      <div className="cards">
        <Kpi icon="⚡" label="Connectées" value={`${configured}/${count}`} hint="variables réellement présentes" />
        <Kpi
          icon="⌘"
          label="Avec écriture"
          value={String(items.filter((i) => i.configured && i.write).length)}
          hint="Jira uniquement, sur le projet SCRUM"
          tone="warn"
        />
        <Kpi icon="◌" label="Suggestions" value={String(suggestions.length)} hint="outils non installés, à trancher" tone="flat" />
      </div>

      <Panel title="Fournisseurs">
        <table className="tbl">
          <thead>
            <tr>
              <th>Service</th>
              <th>Catégorie</th>
              <th>Ce qu'il apporte</th>
              <th>Accès</th>
              <th>État</th>
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.id}>
                <td>
                  <b>{p.label}</b>
                  <div className="mono" style={{ fontSize: 10.5, opacity: 0.7 }}>
                    {p.id}
                  </div>
                </td>
                <td>{CATEGORY_LABEL[p.category] ?? p.category}</td>
                <td>{p.what}</td>
                <td>
                  <div className="chips">
                    {p.write ? <Tag tone="warn">lecture + écriture</Tag> : <Tag tone="mut">lecture seule</Tag>}
                    {p.id === "jira" && p.write_enabled === false && <Tag tone="mut">écriture désactivée</Tag>}
                  </div>
                </td>
                <td>
                  {p.configured ? (
                    <Tag tone="ok">configuré</Tag>
                  ) : (
                    <span>
                      <Tag tone="mut">absent</Tag>
                      <div className="mono" style={{ fontSize: 10.5, opacity: 0.75, marginTop: 4 }}>
                        {p.missing.join(", ")}
                      </div>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="Ce que je suggère ensuite">
        {suggestions.map((s) => (
          <div className="kv" key={s.label} style={{ display: "block", padding: "9px 0" }}>
            <b style={{ display: "block", marginBottom: 3 }}>{s.label}</b>
            <span style={{ fontSize: 11.5 }}>{s.why}</span>
          </div>
        ))}
      </Panel>

      <Panel title="Sécurité des secrets">
        <div className="kv">
          <span>Où vivent les clés</span>
          <b>
            <span className="mono">/opt/agent-factory/secrets/*.env</span> (600, hors dépôt)
          </b>
        </div>
        <div className="kv">
          <span>Ce que le navigateur reçoit</span>
          <b>des booléens et le nom des variables manquantes, jamais une valeur</b>
        </div>
        <div className="kv">
          <span>Saisie d'une clé depuis cette page</span>
          <b>volontairement absente : une clé s'ajoute par le serveur, pas par le navigateur</b>
        </div>
      </Panel>
    </div>
  );
}
