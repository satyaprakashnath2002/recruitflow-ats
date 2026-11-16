# RecruitFlow backend

RecruitFlow's FastAPI backend provides employee authentication, reusable role authorization, employee management, job management, Candidate and Application workflows, private resume handling, interview scheduling/feedback, in-app notifications/reminders, employee-managed Offers with an offer-scoped candidate response capability, versioned health routes, PostgreSQL persistence, and Alembic migrations. Full candidate accounts and general candidate-owned reads remain deferred.

## Prerequisites

- Python 3.11 or newer
- PostgreSQL 14 or newer for local development (or another supported PostgreSQL service)

## Virtual environment and dependencies

From `backend/`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Environment configuration

Copy `.env.example` to `.env` and replace local placeholder values. `.env` is ignored by Git. Set separate values in Development, Staging, and Production; inject production secrets through the hosting environment rather than a committed file. Generate a signing secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`, then assign it to `JWT_SECRET`. It must be at least 32 characters. Never use the example placeholder as a real secret.

`DATABASE_URL` uses SQLAlchemy's `postgresql+psycopg://user:password@host:port/database` format. `CORS_ORIGINS` is a comma-separated list such as `http://localhost:3000`. Pool settings can be adjusted with `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, and `DB_POOL_RECYCLE`.

JWT configuration: `JWT_ALGORITHM` is restricted to HS256/HS384/HS512; defaults are 15-minute access tokens and 7-day refresh tokens. Invitation links expire after 48 hours; password reset links after 60 minutes. These are implementation defaults, configurable through `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS`, `INVITATION_TOKEN_HOURS`, and `PASSWORD_RESET_TOKEN_MINUTES`. Passwords require 12 characters by default (`PASSWORD_MIN_LENGTH`). `NOTIFICATION_POLL_SECONDS` controls the in-process due-reminder poll interval (default 30 seconds, valid 5–3600).

Configure transactional email with `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_EMAIL`, and optional `SMTP_USERNAME`/`SMTP_PASSWORD`. SMTP STARTTLS is required. Without SMTP configuration, invitation and Offer delivery return HTTP 503; password-reset requests retain their generic response and do not reveal email-delivery failure. Use a test sender in automated tests; no real email service is needed for tests.

## Database setup

Create a local PostgreSQL database and role matching your local `DATABASE_URL`, for example database `recruitflow` and a local development role. Keep the password local and do not commit it. Apply the employee identity schema before using auth routes or the CLI:

```powershell
alembic upgrade head
```

Migrations `0001` through `0004` create employee/authentication, job, Candidate/Application, note, private document metadata, and audit tables. Migrations `0005`–`0008` add interview persistence, schedule protections, terminal immutability, and feedback audit support. Migration `0009_in_app_notifications` adds persistent in-app notices and interview reminder jobs. Migration `0010_offer_management` adds Offers, immutable Offer revisions, offer-scoped capability metadata, expiry reminders, and Offer audit/notification targets. Data-bearing milestone downgrades are refused where they would discard workflow, audit, or notification history.

## Alembic

From `backend/`, with the virtual environment active and `.env` configured:

```powershell
alembic history
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Future model modules must be imported into `alembic/env.py` so their tables register on `Base.metadata`. Review generated migrations before applying them. Verify `alembic check` against the intended database after migration.

## Start the API

```powershell
uvicorn app.main:app --reload
```

- Liveness: `GET http://127.0.0.1:8000/api/v1/health`
- Readiness: `GET http://127.0.0.1:8000/api/v1/health/ready`
- OpenAPI UI: `http://127.0.0.1:8000/docs`

Liveness reports process status only. Readiness executes a database check and returns HTTP 503 when the database is unavailable.

## Employee authentication and roles

Implemented routes:

| Method and path | Access |
|---|---|
| `POST /api/v1/auth/login` | Public employee sign-in; returns access/refresh JWTs |
| `GET /api/v1/auth/me` | Authenticated employee profile |
| `POST /api/v1/auth/refresh` | Refresh-token rotation |
| `POST /api/v1/auth/logout` | Revoke the current token session |
| `POST /api/v1/auth/password-reset/request` | Generic email reset request response |
| `POST /api/v1/auth/password-reset/confirm` | One-time verified reset token and new password |
| `GET /api/v1/employees` | Admin only; paginated directory with name/email search and role/status filters |
| `GET /api/v1/employees/{id}` | Admin only; safe employee detail |
| `PATCH /api/v1/employees/{id}` | Admin only; update name, role, or status |
| `POST /api/v1/employees/invitations` | Admin only; sends an employee invitation |
| `POST /api/v1/employees/invitations/accept` | Public one-time invitation acceptance, full name, and password setup |

The four employee roles are `admin`, `hr_manager`, `recruiter`, and `interviewer`. Use `require_roles(...)` from `app.api.dependencies` on protected routes. It returns 401 for missing/invalid/inactive sessions and 403 for roles outside the allowlist. Recruiter assignment scope and unresolved role permissions in later modules must be applied by those modules.

## Job management and public careers API

Migration `0003_job_management` adds `jobs` and `job_assignments`, and extends the append-only audit log to record job events. Apply it with `alembic upgrade head`; existing employee and authentication rows are preserved. Jobs are retained when archived, and assignments use a composite job/employee key to prevent duplicates. Downgrading `0003` is blocked while job rows or job audit events exist so it cannot silently discard them. There is no candidate or application model or endpoint in this milestone.

Job creation requires title, department, employment type, location, work mode, description, and a positive vacancy count. Skills and qualifications default to empty lists. Experience and salary bounds may be omitted; supplied bounds must be nonnegative, and lower values cannot exceed upper values. A supplied application deadline must be a future date. Employment type and work mode are stored as nonblank text because the approved specifications do not define controlled vocabularies.

New jobs start as `draft`. The API enforces only these approved status changes: `draft → published`; `published → paused` or `closed`; `paused → published` or `closed`; `closed → archived`. `archived` is terminal. Invalid transitions return 400. Only Admin and HR Manager can create, edit, and change status. Multiple active Recruiters may be assigned by Admin or HR Manager. Recruiters can list and view only assigned jobs and cannot edit jobs or statuses. Interviewers have no internal job-management access.

Public endpoints do not require authentication:

| Method and path | Behavior |
|---|---|
| `GET /api/v1/public/jobs` | Paginated published jobs with title search and department, employment type, location, work mode, and experience filters |
| `GET /api/v1/public/jobs/{id}` | Published job detail; non-published jobs return 404 |

Public responses include title, department, employment type, location, work mode, experience bounds, description, required skills, qualifications, vacancy count, deadline, and published status. They omit salary, employee IDs, assignments, audit details, and timestamps. Public sorting supports title, department, creation date, and deadline; API default is creation date descending. Experience filters match jobs whose range overlaps the requested range. Deadline expiry does not automatically change job status; public-application eligibility will be implemented with a future application workflow.

Internal routes are `GET/POST /api/v1/jobs`, `GET/PATCH /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}/publish|pause|close|archive`, `GET /api/v1/jobs/{id}/assignments`, and `PUT/DELETE /api/v1/jobs/{id}/assignments/{employee_id}`. Internal listing supports status, search, department, employment type, location, work mode, experience, pagination, and sorting. Create/update and status changes are audited in the same transaction; assignment/removal is also audited. Update audit metadata records changed field names without copying salary values.

Employee management is Admin-only. Directory responses omit password hashes and token/session data. Admins may change `full_name`, `role`, and account `status`; email and credentials are immutable through this API. `full_name` is collected when an invitation is accepted and may be corrected by an Admin. PATCH accepts one or more of those fields. Deactivating an employee revokes all active refresh sessions, which also invalidates access tokens. A disabled employee can be reactivated without restoring revoked sessions. The final active Admin cannot deactivate their own account; Admins also cannot change their own role. Employee management writes append-only audit records for name, role, and status changes.

Passwords use Argon2 hashes. Raw invitation/reset tokens are delivered only in email; database rows retain SHA-256 token hashes. Refresh tokens rotate and are stored hashed. Logout and password reset revoke refresh sessions, which also invalidates associated access tokens. Login errors are generic; password-reset request responses do not reveal account existence.

### First Admin account

After migrations, create the initial Admin only from a trusted local terminal:

```powershell
python -m app.cli create-admin
```

The command prompts for full name, email, and password, stores only the password hash, and refuses to bootstrap if an Admin already exists. There is no public Admin registration endpoint.

### Invitation and password recovery

An authenticated Admin posts an email and one of the four employee roles to `/api/v1/employees/invitations`. The invitation link is sent via configured SMTP and expires. The link uses `FRONTEND_BASE_URL/accept-invitation`; the frontend page is outside this backend milestone, but the token can be submitted to `/api/v1/employees/invitations/accept` with a new password. Links cannot be reused. Re-inviting an address invalidates its previous pending links.

Employees request recovery at `/api/v1/auth/password-reset/request`; if the account is active, the reset link is sent by SMTP. The link uses `FRONTEND_BASE_URL/reset-password`; the frontend page is not part of this milestone, and the token is accepted by `/api/v1/auth/password-reset/confirm`. The response is identical for existing and unknown email addresses. Confirmation consumes the link once and revokes active sessions.

Rate limiting is not implemented inside this API because an in-process limiter would not be reliable across multiple Render instances. Configure request limits at the production edge for login, invitation, and password-reset routes before public deployment.

## Tests

```powershell
pytest
```

Tests create isolated SQLite in-memory schemas and use a controlled email sender; they do not connect to the configured development database or a real SMTP service.

The development extra includes `httpx2`, the supported client for Starlette 1.10's TestClient. Starlette also documents asynchronous ASGI tests with `httpx2.AsyncClient` and `ASGITransport`. Ensure `python -m pip install -e ".[dev]"` completes before running API tests. On restricted Windows environments where Python cannot create local asyncio socket pairs, run Pytest in an environment that permits local process sockets; this is needed for TestClient's in-process transport and does not open an external service port. The final suite was run with TestClient enabled and passed; see the repository requirements traceability for exact counts and disposable PostgreSQL migration/concurrency results.

## Candidate and Application core

Migration `0004_candidate_application_core` adds Candidates, Applications, append-only internal notes, private document metadata, and the corresponding audit targets. Candidate email is trimmed/lowercased and uniquely constrained. Applications are unique per Candidate/Job pair. The database constraint is the final protection against concurrent duplicate submissions.

Candidate/Application endpoints:

| Method and path | Access and behavior |
|---|---|
| `POST/GET /api/v1/candidates` | Admin/HR Manager create and list; duplicate normalized email returns 409 |
| `GET/PATCH /api/v1/candidates/{id}` | Admin/HR Manager global; Recruiter only through an Application in an assigned Job |
| `POST/GET /api/v1/applications` | Admin/HR Manager global; Recruiter only assigned Jobs. Employee creation accepts `recruiter` or `referral` source; public source is server-set |
| `GET/PATCH /api/v1/applications/{id}` | Admin/HR Manager global; assigned Recruiter within assigned Job. PATCH allowlist is empty under MVP-D01 because no independent editable Application business fields exist; candidate, notes, and stage have separate endpoints |
| `POST /api/v1/applications/{id}/stage` | Dedicated stage transition endpoint; actor/transition policy enforced by the backend |
| `POST/GET /api/v1/applications/{id}/notes` | Admin/HR Manager global; assigned Recruiter in assigned Job; notes are append-only and internal |
| `POST/PUT /api/v1/applications/{id}/resume` | Authorized employees upload/replace a PDF; maximum size is 10 MiB |
| `GET /api/v1/documents/{id}` | Authorized employee download through the API; no public URL is returned |
| `POST /api/v1/public/jobs/{job_id}/applications` | Accountless multipart application for a Published Job, including required resume; generic 202 response has no Candidate/Application ID |

Interviewers cannot access Candidate/Application workflows or resumes in this milestone. Recruiters see only Applications attached to Jobs currently assigned to them. Public requests do not return Candidate/Application IDs, candidate records, employee identifiers, notes, audit data, or storage paths.

Deadline evaluation uses `COMPANY_TIMEZONE=Asia/Kolkata`. A dated Published Job accepts public applications through its deadline date in that timezone. A Published Job with no deadline accepts indefinitely while Published. Authorized employee-assisted creation after a supplied deadline is audited.

### Private resume storage

Resume bytes are validated as a structurally readable PDF using `pypdf`; filename and claimed MIME type are not trusted. The maximum is 10 MiB. PostgreSQL stores metadata/checksum only. The current adapter writes opaque UUID-named files under `DOCUMENT_STORAGE_PATH` (default `backend/var/private-documents`) and serves them only after API authorization. No public static route or object URL exists. This local filesystem adapter is suitable for development; Render's filesystem may be ephemeral, so production requires selecting and configuring a durable private object-storage adapter. Provider selection remains unresolved. Superseded resumes are retained pending the approved retention policy.

### Milestone 5 configuration and verification

`backend/.env.example` includes `COMPANY_TIMEZONE` and `DOCUMENT_STORAGE_PATH`. Copy it to `.env` for local development and provide your own local database credentials; do not commit `.env`. Do not point automated tests or migration experiments at a production database.

Run the API and tests from `backend/`:

```powershell
uvicorn app.main:app --reload
pytest -q
```

The default suite uses isolated SQLite databases. To run the opt-in PostgreSQL concurrent-duplicate integration case, first create a disposable database named exactly `recruitflow_m5_test`, apply `alembic upgrade head` to it, and then set `MILESTONE5_TEST_DATABASE_URL` to its URL before running `pytest -q`. The test refuses URLs whose database name differs and cleans up its uniquely named test rows.

## Milestone 6 Phase 2: interview scheduling

Migration `0006_interview_scheduling_audit` permits approved interview scheduling and interviewer-assignment events in the existing append-only AuditLog. Migration `0007_terminal_immutable` protects Completed and Cancelled rounds from all direct SQL updates while retaining the existing deletion and lifecycle guards. Its downgrade restores the prior lifecycle trigger without deleting data. Do not downgrade `0006` when interview audit events exist; it refuses to invalidate audit history.

Implemented Phase 2 routes:

| Method and path | Access and behavior |
|---|---|
| `POST /api/v1/applications/{application_id}/interview-rounds` | HR Manager or assigned Recruiter schedules an Application already at `interview`; optional initial interviewer assignments are accepted |
| `GET /api/v1/applications/{application_id}/interview-rounds` | Admin read-only oversight, HR Manager, assigned Recruiter; Interviewers are denied and must use dedicated assigned-interview routes |
| `GET /api/v1/interview-rounds/{round_id}` | Authorized round detail with role-scoped projection |
| `GET /api/v1/interviews/assigned` | Assigned Interviewer only |
| `PATCH /api/v1/interview-rounds/{round_id}` | HR Manager or assigned Recruiter reschedules a Scheduled round (requires start, end, and IANA timezone) or sends `{"status":"completed"}` / `{"status":"cancelled"}` |
| `POST /api/v1/interview-rounds/{round_id}/cancel` | HR Manager or assigned Recruiter cancels a Scheduled round |
| `POST /api/v1/interview-rounds/{round_id}/complete` | HR Manager or assigned Recruiter completes a Scheduled round |
| `PUT /api/v1/interview-rounds/{round_id}/interviewers/{employee_id}` | HR Manager or assigned Recruiter assigns an active Interviewer |
| `DELETE /api/v1/interview-rounds/{round_id}/interviewers/{employee_id}` | HR Manager or assigned Recruiter removes an active assignment while retaining its row/history |

Admin can read interview oversight but cannot schedule, reschedule, close rounds, or change assignments. Recruiters are scoped to assigned Jobs. Interviewers can read only actively assigned interviews and receive only Candidate full name, applied Job title, round details, their own assignment, and feedback allowed by the approved independent-feedback visibility rule. This does not grant general Candidate/Application, contact, resume, or note access. UTC instants are stored with the applicable IANA timezone retained. Overlap uses half-open intervals: exact adjacency is permitted, any overlap is rejected with 409. PostgreSQL operations lock interviewer Employee rows in deterministic ID order before checking conflicts and persist schedule changes and audit records together.

The M6 PostgreSQL verification tests are opt-in and require a disposable PostgreSQL database named exactly `recruitflow_m6_test`; set `MILESTONE6_TEST_DATABASE_URL` only after creating that disposable database and upgrading it to head. Tests refuse other database names. Never point this variable at the configured development database. The suites verify concurrent creation/rescheduling/assignment and lock ordering, plus direct SQL protection for terminal rounds. Run them with:

```powershell
$env:MILESTONE6_TEST_DATABASE_URL = "postgresql+psycopg://.../recruitflow_m6_test"
pytest -q tests/test_postgres_interview_concurrency.py tests/test_postgres_interview_terminal_trigger.py
```

### Milestone 6 Phase 3: Interview feedback

Feedback submission, own-feedback retrieval, and editing are implemented for the active Interviewer assigned to the exact round. Submit with all four required fields; PATCH supports partial updates but validates the complete resulting record. Each edit preserves the previous values in an immutable internal revision. Revisions are not exposed by an API. HR Manager, Admin, and assigned Recruiter feedback visibility follows the approved role scope in the SDD; Admin remains read-only, Recruiters remain limited to assigned Jobs, and Interviewers cannot access unrelated Applications or Candidate data. Interviewer peer feedback appears in round detail only after that Interviewer submits feedback for the exact round.

Feedback routes:

| Method and path | Access and behavior |
|---|---|
| `POST /api/v1/interview-assignments/{assignment_id}/feedback` | Assigned active Interviewer submits once while the round is Scheduled; duplicate submission returns 409 |
| `GET /api/v1/interview-assignments/{assignment_id}/feedback` | Assigned Interviewer reads their own current feedback |
| `PATCH /api/v1/interview-assignments/{assignment_id}/feedback` | Assigned active Interviewer edits their own feedback while Scheduled; the previous version is preserved |

Feedback audit actions are `interview.feedback_created` and `interview.feedback_modified`. Audit metadata includes identifiers and changed field names, never feedback text. Migration `0008_interview_feedback_audit` adds these actions; it does not add tables. Its downgrade refuses when feedback audit history exists. Do not modify migrations `0001`–`0007`.

The full SQLite-backed suite includes API, validation, authorization, revision, privacy, lifecycle, and migration tests. Opt-in PostgreSQL tests use independent connections and include concurrent feedback submissions, concurrent edits, and feedback editing racing with round completion. Run them only against the explicitly disposable `recruitflow_m6_test` database:

```powershell
$env:MILESTONE6_TEST_DATABASE_URL = "postgresql+psycopg://.../recruitflow_m6_test"
pytest -q tests/test_postgres_interview_concurrency.py tests/test_postgres_interview_terminal_trigger.py
```

Final hiring decisions, application stage changes, notifications/reminders, integrations, and frontend screens are not part of Phase 3.

Apply and inspect migration state:

```powershell
alembic upgrade head
alembic current
alembic check
```

## Final decisions, Application PATCH, and notifications

Final selection/hiring decisions use `POST /api/v1/applications/{id}/stage` and the existing approved stage graph. Only HR Manager may set final outcomes; Admin has no override; Recruiters retain assigned-Job operational stage changes; Interviewers cannot change Application stages. The service locks and refreshes the Application row before checking the transition, then commits stage and audit event atomically. There is no separate HiringDecision table or endpoint.

OQ-015 is resolved by MVP-D01. The current Application schema has no independent editable business fields, so the PATCH allowlist is empty for every role. Empty PATCH requests and supplied/protected fields return 422. Candidate edits, notes, and stage changes use their dedicated APIs.

Authenticated employees use these notification routes:

| Method and path | Behavior |
|---|---|
| `GET /api/v1/notifications?unread=true&event_type=interview_scheduled&page=1&page_size=20` | List only the current employee's notices; supports read/unread and event filters, pagination, newest first |
| `PATCH /api/v1/notifications/{id}` | Mark the recipient's notice as read; other recipients receive 404 |
| `POST /api/v1/notifications/mark-all-read` | Mark the current employee's unread notices as read |

Interview schedule, reschedule, cancellation, completion, and 24h/1h reminders notify only active Interviewers assigned to that exact round. Notices do not include Candidate details or feedback. UTC reminder jobs are superseded on schedule changes, closure, or assignment removal and revalidated when due. The backend lifespan polls every `NOTIFICATION_POLL_SECONDS` (default 30 seconds) from a worker thread; PostgreSQL row locks and unique idempotency keys prevent duplicate persistence across instances. Run a one-shot batch manually with `python -m app.cli process-notifications`. Tests skip starting this worker in the `test` environment.

### Offers (MVP-D06)

Admin and HR Manager may create draft Offers for Applications at `selected`, or reissue for Applications still at `offer` after a prior Offer was withdrawn or expired. No other stage is eligible. Only HR Manager may approve, send, or withdraw. A draft must be submitted for approval, and approval is tied to the exact current revision. One active Offer per Application is enforced by a PostgreSQL partial unique index. Offer status is independent of Application stage. Sending an approved Offer sends an email containing a single-Offer capability link (no terms or PDF) and atomically moves Application `selected → offer`; a reissued Offer leaves it at `offer`. Candidate response does not change Application stage; HR Manager later uses the existing stage endpoint. An Offer may be reissued after it is withdrawn or expired, but not after accepted/rejected response.

Create and revise Offers using multipart form data with `compensation_amount`, `compensation_currency` (uppercase 3-letter code), `compensation_basis`, `expires_at` (timezone-aware ISO 8601), and `letter` (validated private PDF, max 10 MiB). Revisions are immutable. Terms and letter are visible to Admin/HR Manager; the candidate capability accesses only one Offer and its letter. Full candidate accounts and general Candidate/Application reads remain deferred.

| Method and path | Access |
|---|---|
| `POST /api/v1/applications/{application_id}/offers` | Admin/HR Manager create draft |
| `GET /api/v1/offers`, `GET /api/v1/offers/{offer_id}` | Admin/HR Manager list/detail |
| `POST /api/v1/offers/{offer_id}/revisions`, `GET /api/v1/offers/{offer_id}/revisions` | Admin/HR Manager; only draft revisions may be added |
| `GET /api/v1/offers/{offer_id}/revisions/{revision_number}/letter` | Admin/HR Manager private download |
| `POST /api/v1/offers/{offer_id}/submit` | Admin/HR Manager |
| `POST /api/v1/offers/{offer_id}/approve`, `/send`, `/withdraw` | HR Manager only |
| `GET /api/v1/public/offers/current`, `GET /api/v1/public/offers/letter`, `POST /api/v1/public/offers/response` | Public, required `X-Offer-Capability`; capability covers only one Offer and expires at its UTC deadline |

The opaque capability is stored only as a SHA-256 hash. The candidate URL places it in a fragment. Frontend integration must read the fragment, remove it from history, send the value only in `X-Offer-Capability`, and avoid local storage, analytics, or logging. The API CORS middleware explicitly allows this header; set the frontend origin in `CORS_ORIGINS`. Invalid/revoked/expired links receive a generic 404; a repeated response receives 409. Public Offer responses use `Cache-Control: private, no-store`. Configure `FRONTEND_BASE_URL` to the frontend origin and SMTP using the existing `SMTP_*` variables. Offer expiry is processed by the same persistent notification worker; it creates idempotent 3-day/1-day in-app reminders for the approving HR Manager and expires/revokes due sent Offers. Run a one-shot reminder batch with `python -m app.cli process-notifications`.

### Frontend integration handoff

No frontend screens are included in this backend work. For frontend integration, configure an environment-specific API origin (for example `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`) and call versioned routes under `/api/v1`. Add each deployed frontend origin to the backend `CORS_ORIGINS` setting; do not use `*` with credentials. Authenticated employee routes use `Authorization: Bearer <access_token>` and return the common `{ "error": { "status", "message", ... } }` error envelope. A 401 means authentication is absent/invalid; a 403 means an authenticated employee lacks permission. Public careers and application submission routes do not use employee authentication.

For employee UI integration, backend contracts include auth/profile, Candidate/Application and Job routes, interview scheduling/feedback, Offers, and the notification center. Final hiring decisions use `POST /api/v1/applications/{id}/stage`; HR Manager alone may set final outcomes. The general Application PATCH allowlist is empty and returns 422; use dedicated Candidate, note, and stage endpoints. Interviewers must use dedicated assigned-interview routes and must not get broader Candidate/Application or resume access. Public application responses are intentionally generic and contain no internal record identifiers. Offer API details and capability handling are described above. Employee read/write offer flows are Admin/HR Manager scoped; approve/send/withdraw are HR Manager only. Public offer responses must not be combined with general candidate-account features.

For a disposable PostgreSQL verification database, set `DATABASE_URL` explicitly to that database before these commands. The Milestone 5 migration was verified on an isolated PostgreSQL 18.6 cluster: clean upgrade to `0004`, `alembic check` with no drift, empty-schema downgrade to `0003`, and re-upgrade to `0004`. Existing development data must not be downgraded or removed.

### Deployment handoff

For Render, provide `DATABASE_URL`, a unique `JWT_SECRET`, `CORS_ORIGINS`, `FRONTEND_BASE_URL`, `COMPANY_TIMEZONE=Asia/Kolkata`, and SMTP settings as environment secrets/configuration. Run `alembic upgrade head` as a controlled release step against the intended deployment database, then use `/api/v1/health` for liveness and `/api/v1/health/ready` for database readiness. The API process runs the persistent reminder worker; its operations are idempotent across instances. No Docker setup is used.

The included filesystem storage adapter is private but intended for local development. Render filesystems may be ephemeral, so configure a durable private `PrivateObjectStore` implementation before production use; resumes and Offer letters must not be served from public URLs. No production storage vendor has been selected. SMTP configuration is required for invitation, password-reset, and Offer delivery flows. There is no separate deployment SDD file in this repository; deployment-specific configuration and remaining setup are documented here.
