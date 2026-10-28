from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, make_url, text
from sqlalchemy.orm import Session

from app.agent.checkpoints import close_checkpointer, get_checkpointer
from app.config import BACKEND_DIR, get_settings
from app.db.session import get_engine, get_sessionmaker


def reset_cached_db_objects() -> None:
    # The checkpointer and the compiled graph hold connections to whichever database
    # was configured when they were created.
    close_checkpointer()
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def recreate_database(server_url: URL, name: str) -> None:
    admin = create_engine(server_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def test_database(alembic_config: Config) -> Iterator[URL]:
    """Point the app at a fresh `<db>_test` database, migrated to head."""
    reset_cached_db_objects()
    dev_url = make_url(get_settings().database_url)
    test_url = dev_url.set(database=f"{dev_url.database}_test")
    recreate_database(dev_url, test_url.database or "")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        reset_cached_db_objects()
        command.upgrade(alembic_config, "head")
        get_checkpointer()  # create LangGraph's tables now, before any test holds a transaction
        yield test_url
        get_engine().dispose()

    reset_cached_db_objects()


@pytest.fixture
def db_session(test_database: URL) -> Iterator[Session]:
    """A session whose changes, including commits, are rolled back after each test."""
    connection = get_engine().connect()
    transaction = connection.begin()
    # Commits inside the code under test become savepoint releases on this connection.
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
