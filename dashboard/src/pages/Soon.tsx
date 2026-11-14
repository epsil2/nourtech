export function Soon({ title, sprint, body }: { title: string; sprint: string; body: string }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <div className="sub">{sprint}</div>
        </div>
      </div>
      <div className="soon-page">
        <div className="ico" style={{ width: 44, height: 44, borderRadius: 14, fontSize: 18 }}>
          ◈
        </div>
        <h2>Prévu — pas encore livré</h2>
        <p>{body}</p>
        <p style={{ color: "var(--mut-2)", fontSize: 11.5 }}>
          Cette page est volontairement vide : aucun chiffre inventé, aucun écran factice.
        </p>
      </div>
    </>
  );
}
