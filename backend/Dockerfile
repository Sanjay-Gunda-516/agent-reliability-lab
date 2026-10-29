# syntax=docker/dockerfile:1

# ---- build: install runtime dependencies into a virtualenv --------------------------
FROM python:3.14-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY pyproject.toml constraints.txt ./
# Only the [project] dependencies (no dev extras), at the versions the tests ran with.
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > requirements.txt \
 && pip install -r requirements.txt -c constraints.txt \
 && pip uninstall -y pip  # not needed at runtime

# ---- runtime ------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    LOG_FORMAT=json \
    WEB_CONCURRENCY=2

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Application code, migrations and the scenario catalogue. No tests, no .env, no venv.
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app
COPY --chown=root:root scenarios ./scenarios

USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2).status == 200 else 1)"]

# uvicorn reads WEB_CONCURRENCY for the worker count. The dashboard's nginx strips the
# /api prefix, so the API serves at the root.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
