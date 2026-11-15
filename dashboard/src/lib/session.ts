/* Session helpers: the browser holds only an HttpOnly cookie set by the API.
   Everything here reads server-verified identity — never asserts one. */

import { api, ApiError, setActor } from "./api";

export type User = {
  username: string;
  role: string;
  email?: string;
  created_at?: string;
};

export type AuthConfig = {
  auth_enabled: boolean;
  bootstrap_needed?: boolean;
};

export async function me(): Promise<User | null> {
  try {
    const payload = await api.get<{ user?: User }>("/api/v1/auth/me");
    const user = payload?.user;
    if (user?.username) {
      setActor(user.username);
      return user;
    }
    return null;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

export async function login(username: string, password: string): Promise<User> {
  const payload = await api.post<{ user?: User }>("/api/v1/auth/login", { username, password });
  const resolved = (payload?.user ?? payload) as User;
  if (resolved?.username) setActor(resolved.username);
  return resolved;
}

export async function logout(): Promise<void> {
  await api.post("/api/v1/auth/logout", {});
  setActor("");
  localStorage.removeItem("camilia.actor");
}

export const canWrite = (user: User | null): boolean =>
  user?.role === "admin" || user?.role === "operator";
