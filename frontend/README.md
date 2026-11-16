# RecruitFlow Frontend

RecruitFlow's frontend is a Next.js App Router application using TypeScript and React. It provides employee workspace screens and public careers and offer-capability pages. The FastAPI service remains authoritative for authentication, authorization, validation, and workflow transitions.

## Prerequisites

- Node.js 20 or later and npm
- The RecruitFlow backend running at the configured API base URL
- An employee account for authenticated workspace features

## Install and configure

```powershell
cd frontend
npm ci
if (!(Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

Set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` to the backend origin, for example `http://127.0.0.1:8000`. Do not put credentials or secrets in `NEXT_PUBLIC_*` variables; Next.js exposes them to browsers. The file is ignored by Git.

Configure the backend CORS allowlist to include the frontend origin (for local development, `http://localhost:3000`).

## Run locally

```powershell
npm run dev
```

Open <http://localhost:3000>. The backend must be running separately. See the backend README for its environment and PostgreSQL setup.

## Checks

```powershell
npm test
npm run typecheck
npm run build
```

If the local Windows environment locks `.next`, the Next.js config supports a separate build output directory: `$env:NEXT_DIST_DIR = '.next-verification'; npm run build`.

## API and security behavior

- Employee access tokens remain in memory. Only the rotating refresh token is kept in tab-scoped `sessionStorage`.
- Offer capability tokens are read from the URL fragment, removed from browser history immediately, and sent only in `X-Offer-Capability`. They are not persisted.
- Resume and offer-letter downloads use authenticated or offer-capability API requests. No public document URLs are generated.
- Public job payloads are rendered without salary information. Public application responses are shown generically.
- Candidate, Application, Interview, and Offer pages call the backend endpoints directly; UI visibility does not replace API authorization.
- There is no general Application edit form because the approved Application PATCH allowlist is empty.

## Styling

The frontend uses a local CSS design system in `src/app/globals.css` and inline SVG icons, so it does not fetch fonts or icon assets from a third party at runtime. Tailwind CSS could not be installed in the current environment because npm registry DNS resolution failed; this UI therefore uses the existing dependency set and CSS rather than claiming a Tailwind build.

## Known integration limitations

- The Application response does not expose the current resume document ID or a resume metadata list endpoint. The detail view can download a resume uploaded in the current UI session; it cannot discover an existing private document until the backend exposes a scoped metadata contract.
- Interview assignment selection currently uses employee UUIDs because the employee directory API is Admin-only. This keeps the UI within the existing API contract; a discoverable picker requires an approved/backend API contract change.
- Dashboard values are derived from currently accessible backend list endpoints. They are not analytics and may reflect endpoint pagination/visibility constraints.
