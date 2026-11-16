import type { TokenPair } from "@/lib/types";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_PREFIX = "/api/v1";
const REFRESH_KEY = "recruitflow.refresh";

let accessToken: string | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getRefreshToken() {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(REFRESH_KEY);
}

export function saveRefreshToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(REFRESH_KEY, token);
  else window.sessionStorage.removeItem(REFRESH_KEY);
}

function messageFrom(payload: unknown): { message: string; code?: string } {
  if (payload && typeof payload === "object") {
    const root = payload as Record<string, unknown>;
    const raw = root.error ?? root.detail ?? root;
    if (typeof raw === "string") return { message: raw };
    const error = raw as Record<string, unknown>;
    if (error && typeof error === "object") {
      const message = typeof error.message === "string" ? error.message : "Request failed";
      const code = typeof error.status === "string" ? error.status : undefined;
      return { message, code };
    }
  }
  return { message: "Request failed" };
}

async function refreshAccess(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  refreshInFlight = (async () => {
    try {
      const response = await fetch(`${API_BASE}${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Refresh rejected");
      const tokens = (await response.json()) as TokenPair;
      accessToken = tokens.access_token;
      saveRefreshToken(tokens.refresh_token);
      return accessToken;
    } catch {
      accessToken = null;
      saveRefreshToken(null);
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export interface RequestOptions extends RequestInit {
  authenticated?: boolean;
  offerCapability?: string;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { authenticated = true, offerCapability, headers: suppliedHeaders, ...init } = options;
  const execute = async (token: string | null) => {
    const headers = new Headers(suppliedHeaders);
    headers.set("Accept", "application/json");
    if (authenticated && token) headers.set("Authorization", `Bearer ${token}`);
    if (offerCapability) headers.set("X-Offer-Capability", offerCapability);
    if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    return fetch(`${API_BASE}${API_PREFIX}${path}`, { ...init, headers, cache: "no-store" });
  };

  let response = await execute(accessToken);
  if (response.status === 401 && authenticated && !path.startsWith("/auth/")) {
    const refreshed = await refreshAccess();
    if (refreshed) response = await execute(refreshed);
    else if (typeof window !== "undefined") window.dispatchEvent(new Event("recruitflow:session-expired"));
  }

  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = messageFrom(payload);
    throw new ApiError(error.message, response.status, error.code);
  }
  return payload as T;
}

export async function apiBlob(path: string, offerCapability?: string, authenticated = true): Promise<Blob> {
  const send = (token: string | null) => {
    const headers = new Headers();
    if (authenticated && token) headers.set("Authorization", `Bearer ${token}`);
    if (offerCapability) headers.set("X-Offer-Capability", offerCapability);
    return fetch(`${API_BASE}${API_PREFIX}${path}`, { headers, cache: "no-store" });
  };
  let response = await send(accessToken);
  if (response.status === 401 && authenticated && !offerCapability) {
    const refreshed = await refreshAccess();
    if (refreshed) response = await send(refreshed);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = messageFrom(payload);
    throw new ApiError(error.message, response.status, error.code);
  }
  return response.blob();
}

export const jsonBody = (value: unknown): string => JSON.stringify(value);
