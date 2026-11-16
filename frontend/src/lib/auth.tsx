"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, jsonBody, saveRefreshToken, setAccessToken } from "@/lib/api";
import type { Employee, TokenPair } from "@/lib/types";

interface AuthContextValue {
  employee: Employee | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function establishSession(tokens: TokenPair): Promise<Employee> {
  setAccessToken(tokens.access_token);
  saveRefreshToken(tokens.refresh_token);
  return api<Employee>("/auth/me");
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();

  const clearSession = useCallback(() => {
    setAccessToken(null);
    saveRefreshToken(null);
    setEmployee(null);
  }, []);

  useEffect(() => {
    let active = true;
    const expired = () => {
      clearSession();
      router.replace("/login?expired=1");
    };
    window.addEventListener("recruitflow:session-expired", expired);
    const restore = async () => {
      try {
        const token = window.sessionStorage.getItem("recruitflow.refresh");
        if (!token) return;
        const pair = await api<TokenPair>("/auth/refresh", {
          method: "POST", authenticated: false, body: jsonBody({ refresh_token: token }),
        });
        const profile = await establishSession(pair);
        if (active) setEmployee(profile);
      } catch {
        clearSession();
      } finally {
        if (active) setReady(true);
      }
    };
    void restore();
    return () => {
      active = false;
      window.removeEventListener("recruitflow:session-expired", expired);
    };
  }, [clearSession, router]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api<TokenPair>("/auth/login", {
      method: "POST", authenticated: false, body: jsonBody({ email, password }),
    });
    const profile = await establishSession(tokens);
    setEmployee(profile);
  }, []);

  const logout = useCallback(async () => {
    try { await api<void>("/auth/logout", { method: "POST" }); }
    finally { clearSession(); router.replace("/login"); }
  }, [clearSession, router]);

  const value = useMemo(() => ({ employee, ready, login, logout }), [employee, ready, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
