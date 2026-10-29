import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.lab_models  # noqa: F401  (registers tables on Base.metadata)
import app.db.models  # noqa: F401
from app.agent.checkpoints import LANGGRAPH_TABLES
from app.config import get_settings
from app.db.base import Base

config = context.config

# Use alembic.ini's logging only for the plain `alembic` command; when migrations run
# in-process (app.migrate, tests) the app's own logging is already set up.
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj: object, name: str | None, type_: str, *_: object) -> bool:
    # LangGraph's checkpoint tables are created and migrated by LangGraph itself.
    if type_ == "table" and name in LANGGRAPH_TABLES:
        return False
    table = getattr(obj, "table", None)
    return not (type_ in ("index", "column") and getattr(table, "name", None) in LANGGRAPH_TABLES)


# The URL is read from app settings rather than alembic.ini so credentials never
# live in a committed file (and '%' in passwords doesn't trip configparser).
database_url = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
