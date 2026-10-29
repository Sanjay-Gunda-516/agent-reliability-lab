# Agent Reliability Lab

Testing, evaluation and regression platform for an AI customer-support agent.
Architecture and design decisions: [docs/architecture/phase-0-overview.md](docs/architecture/phase-0-overview.md).

> Work in progress. A full README with architecture diagrams, measured metrics and
> screenshots will be added once the evaluation suite has produced real results.

## Local development

Requirements: Python 3.12+, Docker.

```bash
# 1. Configure the backend (then set a real POSTGRES_PASSWORD and matching DATABASE_URL)
cp backend/.env.example backend/.env

# 2. Start PostgreSQL (exposed on localhost:5433)
docker compose up -d db

# 3. Install the backend into a virtual environment
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 4. Apply database migrations
.venv/bin/alembic upgrade head

# 5. Run the API
.venv/bin/uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Liveness: `GET /health`
- Readiness (checks the database): `GET /health/ready`

## Checks

Run from `backend/`:

```bash
.venv/bin/pytest                      # all tests (integration tests need the db container)
.venv/bin/pytest -m "not integration" # unit tests only, no database needed
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

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
