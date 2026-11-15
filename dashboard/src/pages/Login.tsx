import { useState } from "react";
import { ApiError } from "../lib/api";
import { login } from "../lib/session";

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      setPassword("");
      onSignedIn();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Identifiants refusés.");
      } else {
        setError(err instanceof Error ? err.message : "Connexion impossible.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell shell--auth">
      <main className="auth">
        <form className="card auth__card" onSubmit={submit}>
          <h1>Camilia</h1>
          <p className="muted">
            Plan de contrôle de livraison. L'accès est nominatif : chaque action est enregistrée avec
            l'identité vérifiée par le serveur.
          </p>
          <label className="field">
            <span>Identifiant</span>
            <input
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy}
            />
          </label>
          <label className="field">
            <span>Mot de passe</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="btn btn--primary" type="submit" disabled={busy || !username || !password}>
            {busy ? "Connexion…" : "Se connecter"}
          </button>
        </form>
      </main>
    </div>
  );
}
