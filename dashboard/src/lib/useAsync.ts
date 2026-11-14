import { useCallback, useEffect, useState } from "react";
import type { Async } from "./api";

/** Fetch on mount; `refresh` re-runs it. Never renders stale data as fresh. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): [Async<T>, () => void] {
  const [state, setState] = useState<Async<T>>({ state: "loading" });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setState((prev) => (prev.state === "ok" ? prev : { state: "loading" }));
    fn()
      .then((data) => alive && setState({ state: "ok", data }))
      .catch((err: unknown) =>
        alive && setState({ state: "error", error: err instanceof Error ? err.message : String(err) }),
      );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  return [state, refresh];
}
