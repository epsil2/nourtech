import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode; page?: string };
type State = { error: Error | null };

/**
 * One broken page must not blank the whole dashboard: we keep the shell (nav,
 * theme switch) alive and show the failure in place, with the message the
 * browser actually produced instead of a generic "something went wrong".
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page crash", this.props.page, error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.page !== this.props.page && this.state.error) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="panel">
        <div className="panel-head">
          <h2>⚠ Cette page n'a pas pu s'afficher</h2>
          <span className="chip">{this.props.page ?? "page"}</span>
        </div>
        <p className="sub">
          Le reste du tableau de bord fonctionne. Rechargez la page ; si l'erreur persiste, transmettez le message
          ci-dessous.
        </p>
        <pre className="mono" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
          {error.message}
        </pre>
        <button className="btn ghost" type="button" onClick={() => this.setState({ error: null })} style={{ marginTop: 10 }}>
          <span className="pict">⟳</span> Réessayer
        </button>
      </div>
    );
  }
}
