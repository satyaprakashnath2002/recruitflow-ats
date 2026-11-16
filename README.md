# RecruitFlow – Recruitment Management System

RecruitFlow is a single-company recruitment management system built with a spec-driven workflow. The backend implements employee identity/RBAC, jobs, candidates/applications, private resumes, interviews/feedback, final application stage decisions, in-app notifications, and the MVP-D06 offer-scoped workflow. The consolidated backend MVP is still under final verification. Frontend features are not implemented.

## Technology

- Frontend: Next.js, TypeScript, Vitest
- Backend: Python, FastAPI, SQLAlchemy, Alembic, Pytest
- Database: PostgreSQL
- Authentication: JWT access and refresh tokens for employees
- Hosting: Vercel (frontend), Render (backend)
- CI/CD: GitHub Actions
- Docker is not used.

## Repository layout

```text
frontend/   Next.js application
backend/    FastAPI application
specs/      Approved product specifications and acceptance criteria
docs/       Supporting technical documentation
```

## Specifications

The SDD set is in [`specs/`](specs/README.md). Begin with the [project overview](specs/01-project-overview.md), [functional requirements](specs/03-functional-requirements.md), and [decisions and open questions](specs/12-decisions-and-open-questions.md). Approved decisions remain authoritative. The consolidated backend resolutions are MVP-D01..D06 in the decision register. MVP-D06 permits an offer-scoped candidate capability; full candidate accounts remain deferred.

## Prerequisites

- Node.js and npm
- Python 3.11 or newer
- PostgreSQL 14 or newer for the backend

## Run the frontend

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

## Run the backend

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Configure PostgreSQL and `backend/.env` from `backend/.env.example`, apply migrations with `alembic upgrade head`, then start the API. See the [backend README](backend/README.md) for PowerShell setup, environment variables, Admin bootstrap, migration, API, and test instructions. OpenAPI UI is at http://127.0.0.1:8000/docs.

## Configuration and secrets

Keep frontend and backend configuration independent. Use environment variables and ignored `.env` files for configuration. Never commit credentials, database passwords, JWT secrets, SMTP credentials, or object-storage secrets. Example environment files contain placeholders only.

## Spec-driven workflow

Before implementing a feature, read its approved specification in `specs/`, capture requirements and acceptance criteria in an implementation plan, implement only that scope, add automated tests, and report changed files and test commands/results. Do not infer or add product requirements that are absent from approved specifications.
