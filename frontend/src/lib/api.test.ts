import { afterEach, describe, expect, it, vi } from "vitest";
import { api, setAccessToken } from "@/lib/api";
import { queryString } from "@/lib/use-remote";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("frontend API helpers", () => {
  it("omits empty query values and encodes the remaining values", () => {
    expect(queryString({ page: 2, search: "R&D / People", empty: "", missing: undefined })).toBe("page=2&search=R%26D+%2F+People");
  });

  it("does not attach an employee bearer token to public capability requests", async () => {
    setAccessToken("employee-access-token");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBeNull();
      expect(headers.get("X-Offer-Capability")).toBe("offer-only-capability");
      return new Response(JSON.stringify({ job_title: "Designer" }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/public/offers/current", { authenticated: false, offerCapability: "offer-only-capability" })).resolves.toEqual({ job_title: "Designer" });
  });

  it("attaches the in-memory employee token to authenticated API calls", async () => {
    setAccessToken("access-token");
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer access-token");
      return new Response(JSON.stringify({ id: "employee-1" }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    await expect(api("/auth/me")).resolves.toEqual({ id: "employee-1" });
  });

  it("preserves plain FastAPI detail messages and HTTP status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Invalid transition" }), { status: 400, headers: { "Content-Type": "application/json" } })));
    await expect(api("/applications/example/stage", { method: "POST", body: JSON.stringify({ stage: "hired" }) })).rejects.toMatchObject({ message: "Invalid transition", status: 400 });
  });

  it("shows FastAPI validation locations and reasons", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { status: "error", message: "Invalid request", details: [{ loc: ["body", "application_deadline"], msg: "Application deadline must be a future date" }] } }), { status: 422, headers: { "Content-Type": "application/json" } })));
    await expect(api("/jobs", { method: "POST", body: JSON.stringify({ application_deadline: "2026-11-19" }) })).rejects.toMatchObject({ message: "Invalid request — application_deadline: Application deadline must be a future date", status: 422 });
  });
});
