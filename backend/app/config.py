from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    # Either a full DATABASE_URL, or the parts below (used by the containers, which reach
    # Postgres at db:5432 with the same credentials the host uses at localhost:5433).
    database_url: str = ""
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None
    postgres_db: str | None = None
    # Optional so the API, tests and migrations run without it; the agent endpoint
    # returns 503 when it's missing.
    anthropic_api_key: SecretStr | None = None

    # Optional LangSmith tracing. Our own database traces stay the system of record.
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "agent-reliability-lab"
    langsmith_endpoint: str | None = None  # e.g. the EU region; default is the US API
    langsmith_hide_content: bool = False  # send structure and timing, not prompts or replies

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "Settings":
        if self.database_url:
            return self
        parts = (self.postgres_host, self.postgres_user, self.postgres_password, self.postgres_db)
        if not all(parts):
            raise ValueError(
                "database_url is required: set DATABASE_URL, or POSTGRES_HOST, POSTGRES_USER, "
                "POSTGRES_PASSWORD and POSTGRES_DB"
            )
        assert self.postgres_password is not None
        self.database_url = URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
