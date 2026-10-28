from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    # Optional so the API, tests and migrations run without it; the agent endpoint
    # returns 503 when it's missing.
    anthropic_api_key: SecretStr | None = None

    # Optional LangSmith tracing. Our own database traces stay the system of record.
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "agent-reliability-lab"
    langsmith_endpoint: str | None = None  # e.g. the EU region; default is the US API
    langsmith_hide_content: bool = False  # send structure and timing, not prompts or replies


@lru_cache
def get_settings() -> Settings:
    return Settings()
