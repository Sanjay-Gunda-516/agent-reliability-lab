# Agent Reliability Lab

Testing, evaluation and regression platform for an AI customer-support agent.
Architecture and design decisions: [docs/architecture/phase-0-overview.md](docs/architecture/phase-0-overview.md).

> Work in progress. A full README with architecture diagrams, measured metrics and
> screenshots will be added once the evaluation suite has produced real results.

## Run it locally

Requirements: Docker (Desktop) with Compose v2.

```bash
cp backend/.env.example backend/.env   # then set a real POSTGRES_PASSWORD (and DATABASE_URL to match)
docker compose up --build              # http://localhost:8080
```

That starts four services on an internal network:

| Service | What it does | Reachable from the host |
|---|---|---|
| `db` | PostgreSQL 17, data in the `pgdata` volume | `127.0.0.1:5433` (for host-side tests and tools) |
| `migrate` | Runs once: Alembic migrations, LangGraph checkpoint tables, demo seed if empty | — (exits 0, or the stack stops) |
| `api` | FastAPI, starts only after `migrate` succeeds | — (through the dashboard at `/api`) |
| `web` | nginx serving the dashboard and forwarding `/api` to `api` | `127.0.0.1:8080` |

Everything runs without Claude or LangSmith credentials. Anything that needs Claude (live
chat, starting test runs, approval decisions) answers 503 until `ANTHROPIC_API_KEY` is set
in `backend/.env`. See [docs/containers.md](docs/containers.md) for the images, health
checks and configuration.

| Task | Command |
|---|---|
| First start (build images) | `docker compose up --build` (add `-d` to run in the background) |
| Normal start | `docker compose up -d` |
| Status and health | `docker compose ps -a` |
| Logs | `docker compose logs -f api` (or `web`, `db`, `migrate`) |
| Stop, keep everything | `docker compose stop` (resume with `docker compose start`) |
| Remove containers, keep data | `docker compose down` |
| Full reset, **deletes the database** | `docker compose down -v` |
| Rebuild after dependency or code changes | `docker compose up -d --build` |
| Migration status | `docker compose run --rm migrate alembic current` (and `alembic history`) |
| Re-run migrations | `docker compose run --rm migrate` |
| Postgres shell | `docker compose exec db psql -U arl agent_reliability_lab` |

Ports can be changed from the shell: `DASHBOARD_PORT=9000 POSTGRES_HOST_PORT=5544 docker compose up -d`.

### Developing with hot reload

Run only the database in Docker and the API and dashboard on the host:

```bash
docker compose up -d db
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head          # or: python -m app.migrate (also seeds an empty store)
.venv/bin/uvicorn app.main:app --reload # http://localhost:8000/docs
cd ../frontend && npm install && npm run dev   # http://localhost:5173
```

## Dashboard

A React + TypeScript dashboard for runs, scenario results, traces, regression reports,
approvals and agent versions. It shows only what the API returns. In development:

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173 (proxies /api to http://localhost:8000)
npm test        # plus: npm run lint, npm run typecheck, npm run build
```

See [frontend/README.md](frontend/README.md) for the structure and design tokens.

## Checks

Backend, from `backend/` (with `docker compose up -d db` running):

```bash
.venv/bin/pytest                      # all tests (integration tests need the db container)
.venv/bin/pytest -m "not integration" # unit tests only, no database needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

Frontend, from `frontend/`: `npm test`, `npm run lint`, `npm run typecheck`, `npm run build`.

Integration tests run against a separate `<database>_test` database that is
recreated and migrated on every test session, so they never touch development data.

Tests that call the real Claude API are skipped unless you pass `--live`
(`.venv/bin/pytest --live -k live`). They need `ANTHROPIC_API_KEY` in `backend/.env`.

## Scenarios

Test scenarios for the agent live in `backend/scenarios/*.yaml`. See
[docs/scenarios.md](docs/scenarios.md) for the format.

```bash
.venv/bin/python -m app.seed             # reset the demo store
.venv/bin/python -m app.scenarios check  # validate scenario files
.venv/bin/python -m app.scenarios sync   # load them into the database
```

## Observability

Every agent execution is traced (`GET /traces/{execution_id}`), logs carry correlation
ids, and LangSmith tracing is optional. See [docs/observability.md](docs/observability.md).

## Regression testing

Compare a candidate agent version with a baseline on the same scenarios. A deterministic
gate fails on new critical failures or a task-success drop, and flipped variants can be
rerun to tell noise from real regressions. See [docs/regression.md](docs/regression.md).

```bash
.venv/bin/python -m app.evaluation compare <baseline-run> <candidate-run>   # exit 1 on fail
```

## Human approvals

Refunds over $500 pause the conversation until a support team member approves or
rejects them (`/approvals`); the same execution then resumes. See
[docs/human-in-the-loop.md](docs/human-in-the-loop.md).

## Evaluations

Scenario runs are graded by deterministic checks, with an optional LLM judge for what
the customer was told. See [docs/evaluation.md](docs/evaluation.md) for the method.

```bash
.venv/bin/python -m app.evaluation run --tag happy-path --judge   # paid; asks first
.venv/bin/python -m app.evaluation show <run-id>
```
