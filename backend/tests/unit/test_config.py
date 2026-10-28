import pytest
from pydantic import ValidationError

from app.config import Settings

DB_URL = "postgresql+psycopg://user:pass@db.example:5432/app"


def load_settings() -> Settings:
    # Skip backend/.env so the tests only see what they set explicitly.
    return Settings(_env_file=None)


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", "production")

    settings = load_settings()

    assert settings.database_url == DB_URL
    assert settings.environment == "production"


def test_environment_defaults_to_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert load_settings().environment == "development"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        load_settings()


def test_rejects_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", "staging")

    with pytest.raises(ValidationError, match="environment"):
        load_settings()
