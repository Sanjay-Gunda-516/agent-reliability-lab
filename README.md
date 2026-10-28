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
