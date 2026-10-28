import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL

from app.db.session import get_engine

pytestmark = pytest.mark.integration


def current_revision() -> str | None:
    with get_engine().connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def test_runs_against_isolated_test_database(test_database: URL) -> None:
    assert test_database.database is not None
    assert test_database.database.endswith("_test")
    assert get_engine().url.database == test_database.database


def test_database_is_at_head_revision(alembic_config: Config) -> None:
    head = ScriptDirectory.from_config(alembic_config).get_current_head()

    assert current_revision() == head


def test_models_match_migrations(alembic_config: Config) -> None:
    # Fails if a model changed without a corresponding migration.
    command.check(alembic_config)


def test_migrations_downgrade_and_reapply_cleanly(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    assert current_revision() is None

    command.upgrade(alembic_config, "head")
    assert current_revision() == ScriptDirectory.from_config(alembic_config).get_current_head()
