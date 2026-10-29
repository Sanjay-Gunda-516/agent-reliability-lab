# Containers

`docker compose up --build` runs the whole lab locally, as production-like as a laptop
allows:

```
browser ──► web (nginx :8080) ──/api──► api (FastAPI :8000) ──► db (Postgres :5432)
                                            ▲
                         migrate (runs once) ┘ (schema, checkpoint tables, seed)
```

All four services share one internal Compose network, `lab`. Only two ports are
published, both on `127.0.0.1`: the dashboard (`DASHBOARD_PORT`, default 8080) and
Postgres (`POSTGRES_HOST_PORT`, default 5433). Postgres is published so that host-side
pytest, Alembic and `uvicorn --reload` keep working. The API is not published; it is
reached through the dashboard at `/api`.

## Images

| Image | Base | Size | Runs as | Contains |
|---|---|---|---|---|
| `agent-reliability-lab/api` | `python:3.14-slim` | ~450 MB | `app` (uid 10001) | A virtualenv with the runtime dependencies, plus `app/`, `alembic/` and `scenarios/` |
| `agent-reliability-lab/web` | `nginxinc/nginx-unprivileged:1.29-alpine` | ~85 MB | `nginx` (uid 101) | The built dashboard and the nginx configuration |

**API image** ([backend/Dockerfile](../backend/Dockerfile)), built in two stages:

1. **Build stage.** Installs only the `[project]` dependencies from `pyproject.toml`,
   pinned by `constraints.txt` to the exact versions the test suite runs against. pip
   is then removed.
2. **Runtime stage.** Copies that virtualenv and the source; the code is owned by root
   and runs as the unprivileged `app` user.

There are no tests, dev tools, `.env` or local `.venv` in the image: `.dockerignore`
is an allow-list. uvicorn starts with `WEB_CONCURRENCY` workers (default 2) and runs no
migrations itself. Of the size, about 190 MB is real dependencies (SQLAlchemy, the
Anthropic SDK, LangGraph, psycopg, uvloop) and the rest is the Python base image.

To refresh the pins after changing dependencies:

```bash
.venv/bin/pip freeze --exclude-editable > constraints.txt
```

Then re-add the header comment.

**Dashboard image** ([frontend/Dockerfile](../frontend/Dockerfile)), also two stages:

1. **Build stage.** Node runs `npm ci && npm run build`, which type-checks and builds.
2. **Runtime stage.** Only `dist/` and the nginx configuration; no Node.

The nginx configuration ([frontend/deploy](../frontend/deploy)):

- Forwards `/api/...` to `${API_UPSTREAM}`, keeping the original URL-encoded path and
  query.
- Re-resolves the API's name through `${NGINX_RESOLVER}` every 10 s, so a recreated
  API container is found without restarting nginx.
- Falls back to `index.html` for client routes, so refreshing `/traces/<id>` works.
- Caches fingerprinted `/assets/` for a year, with `no-cache` on everything else.
- Sends `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy` on every response.

## Migrations

The `migrate` service runs `python -m app.migrate` once per `up`. It:

1. takes a Postgres advisory lock, so a second runner waits instead of racing;
2. applies Alembic migrations;
3. creates LangGraph's checkpoint tables;
4. seeds the demo store, but only if it's empty, so an existing store is never reset;
5. exits 0.

The API depends on it with `service_completed_successfully`. A failed migration
therefore stops the stack: `docker compose ps -a` shows `migrate` exited with code 1,
and `docker compose logs migrate` has the error. The lock is held on an autocommit
connection, because LangGraph's `CREATE INDEX CONCURRENTLY` would otherwise wait on it
forever.

## Health checks

| Service | Check | Healthy means |
|---|---|---|
| `db` | `pg_isready` | Postgres accepts connections |
| `migrate` | exit code | 0 = schema, checkpoint tables and seed are ready |
| `api` | `GET /health/ready` inside the container | FastAPI is up **and** can query the database |
| `web` | `GET /api/health/ready` through nginx | nginx is up **and** reaches a ready API through its proxy |

`GET /nginx-health` reports nginx alone, for when you need to tell an nginx problem
from an API problem. Start-up order follows these checks:

```
db healthy → migrate exits 0 → api healthy → web
```

## Configuration and secrets

Everything comes from `backend/.env` (see [backend/.env.example](../backend/.env.example)):

- **Required:** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, plus `DATABASE_URL`
  for host-side tools.
- **Defaults:** `LOG_LEVEL`, `LOG_FORMAT`.
- **Optional:** `ANTHROPIC_API_KEY`, `LANGSMITH_*`.

How the containers use it:

- **Database connection.** Compose blanks `DATABASE_URL` for the API and `migrate`
  containers and sets `POSTGRES_HOST=db`. The app then builds the URL from the same
  `POSTGRES_*` values, so there is one password, in one file.
- **Environment.** The containers always set `ENVIRONMENT=production`.
- **What reaches each container.** The values are passed at run time through
  `env_file`; nothing is baked into an image. Checked: neither image contains a `.env`
  file or the values of `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY` or `POSTGRES_PASSWORD`.
  Note that the `db` container also receives the other variables in `backend/.env`; it
  only uses the `POSTGRES_*` ones.
- **Without Claude or LangSmith credentials.** The stack starts and the dashboard
  works; Claude-backed endpoints return 503.

## Persistence

Postgres data lives in the named volume `agent-reliability-lab_pgdata`:

- `stop`/`start`, `down`/`up` and image rebuilds keep it.
- Only `docker compose down -v` deletes it.
