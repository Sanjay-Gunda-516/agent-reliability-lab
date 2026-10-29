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


def test_database_url_can_be_built_from_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in {
        "POSTGRES_HOST": "db",
        "POSTGRES_USER": "arl",
        "POSTGRES_PASSWORD": "p@ss/word",
        "POSTGRES_DB": "lab",
    }.items():
        monkeypatch.setenv(key, value)

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://arl:p%40ss%2Fword@db:5432/lab"


def test_explicit_database_url_wins_over_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("POSTGRES_HOST", "db")

    assert load_settings().database_url == DB_URL


def test_empty_database_url_with_missing_parts_names_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("POSTGRES_HOST", "db")

    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        load_settings()


def test_rejects_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", "staging")

    with pytest.raises(ValidationError, match="environment"):
        load_settings()
