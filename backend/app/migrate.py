"""Prepare the database, once, before the API starts. Run from backend/:

    python -m app.migrate

Applies Alembic migrations, creates LangGraph's checkpoint tables, and seeds the demo
store if it's empty (an existing store is left alone). A Postgres advisory lock is held
throughout, so a second runner waits instead of racing. Any failure exits non-zero.
"""

import logging
import sys
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

from app.agent.checkpoints import _psycopg_url
from app.config import BACKEND_DIR, get_settings
from app.db.models import Customer
from app.db.session import get_sessionmaker
from app.observability.logging import configure_logging
from app.seed import reset_demo_data

logger = logging.getLogger("app.migrate")

# Arbitrary, fixed key: every migration runner takes the same lock.
MIGRATION_LOCK_ID = 727_101


def _seed_if_empty() -> bool:
    with get_sessionmaker()() as db:
        if db.scalar(select(func.count()).select_from(Customer)):
            return False
        reset_demo_data(db, datetime.now(UTC))
        db.commit()
        return True


def migrate() -> None:
    settings = get_settings()
    # Its own connection without the app's statement timeout: waiting for another
    # runner to finish can take longer than any single query should.
    lock_engine = create_engine(settings.database_url, poolclass=NullPool)
    with lock_engine.connect() as lock:
        logger.info("Waiting for the migration lock")
        lock.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        try:
            config = Config(str(BACKEND_DIR / "alembic.ini"))
            command.upgrade(config, "head")
            logger.info("Database schema is at head")
            url = _psycopg_url(settings.database_url)
            with Connection.connect(url, autocommit=True, row_factory=dict_row) as conn:
                PostgresSaver(conn).setup()
            logger.info("LangGraph checkpoint tables are ready")
            if _seed_if_empty():
                logger.info("Seeded the demo store")
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
            lock.commit()


def main() -> int:
    configure_logging()
    try:
        migrate()
    except Exception:
        logger.exception("Database preparation failed")
        return 1
    logger.info("Database ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
