"""LangGraph's Postgres checkpointer: conversation state that survives between requests.

Each agent execution is a LangGraph thread whose id is the execution_id. The tables
(checkpoints, checkpoint_writes, ...) belong to LangGraph and are created by its own
`setup()`, not by our Alembic migrations.
"""

from functools import lru_cache

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import make_url

from app.config import get_settings

# Tables LangGraph creates and migrates itself; Alembic must leave them alone.
LANGGRAPH_TABLES = frozenset(
    {"checkpoints", "checkpoint_writes", "checkpoint_blobs", "checkpoint_migrations"}
)

_setup_done = False


def _psycopg_url(sqlalchemy_url: str) -> str:
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@lru_cache
def get_checkpointer() -> PostgresSaver:
    pool = ConnectionPool(
        _psycopg_url(get_settings().database_url),
        min_size=1,
        max_size=10,
        # Settings LangGraph's saver requires; the timeout matches the app's engine.
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c statement_timeout=5000",
        },
        open=True,
    )
    saver = PostgresSaver(pool)  # type: ignore[arg-type]
    global _setup_done
    if not _setup_done:
        # Once per process: LangGraph's migrations include CREATE INDEX CONCURRENTLY,
        # which waits for every open transaction in the database.
        saver.setup()
        _setup_done = True
    return saver


def close_checkpointer() -> None:
    if get_checkpointer.cache_info().currsize:
        pool = get_checkpointer().conn
        if isinstance(pool, ConnectionPool):
            pool.close()
        get_checkpointer.cache_clear()


def thread_config(execution_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": execution_id}}
