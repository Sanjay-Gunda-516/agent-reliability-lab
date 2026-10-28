from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.db.models  # noqa: F401  (registers tables on Base.metadata)
from app.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    # Keep app loggers alive when migrations run in-process (e.g. from tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# The URL is read from app settings rather than alembic.ini so credentials never
# live in a committed file (and '%' in passwords doesn't trip configparser).
database_url = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
