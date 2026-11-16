import { useCallback, useEffect, useState } from "react";

export type Theme = "dark" | "light";

const KEY = "camilia.theme";

function initial(): Theme {
  const stored = localStorage.getItem(KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#eef1f7" : "#07070d");
}

/** Applied before React mounts so the first paint already matches the choice. */
export function bootTheme(): void {
  apply(initial());
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(initial);

  useEffect(() => {
    apply(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const set = useCallback((next: Theme) => setTheme(next), []);
  return [theme, set];
}
