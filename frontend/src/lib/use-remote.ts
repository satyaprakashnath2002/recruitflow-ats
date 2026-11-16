"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";

export function useRemote<T>(loader: () => Promise<T>, dependencies: readonly unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((value) => value + 1), []);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    loader().then((result) => { if (alive) setData(result); }).catch((reason: unknown) => {
      if (alive) setError(reason instanceof ApiError ? reason.message : reason instanceof Error ? reason.message : "Unexpected request error");
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // The caller supplies stable identity dependencies for this resource.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, version]);
  return { data, loading, error, reload, setData };
}

export function queryString(values: Record<string, string | number | undefined | null>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value !== undefined && value !== null && String(value) !== "") params.set(key, String(value));
  return params.toString();
}
