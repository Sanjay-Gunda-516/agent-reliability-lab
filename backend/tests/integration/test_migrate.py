"""app.migrate: schema, checkpoint tables and demo seed, once, under a lock."""

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, inspect, make_url, select, text
from sqlalchemy.pool import NullPool

from app import migrate as migrate_module
from app.agent.checkpoints import LANGGRAPH_TABLES
from app.config import get_settings
from app.db.models import Customer, Order
from app.db.session import get_sessionmaker
from app.seed import reset_demo_data
from tests.integration.conftest import recreate_database, reset_cached_db_objects

pytestmark = pytest.mark.integration


def customers() -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(Customer)) or 0


def test_is_idempotent_and_never_resets_an_existing_store() -> None:
    with get_sessionmaker()() as db:
        reset_demo_data(db, datetime.now(UTC))
        order = db.scalars(select(Order).limit(1)).one()
        order.status = "cancelled"  # a change a live chat might have made
        changed_id = order.id
        db.commit()

    assert migrate_module.main() == 0
    assert migrate_module.main() == 0

    with get_sessionmaker()() as db:
        order = db.get(Order, changed_id)
        assert order is not None and order.status == "cancelled"


def test_seeds_an_empty_store() -> None:
    with get_sessionmaker()() as db:
        for table in ("returns", "order_items", "orders", "customers"):
            db.execute(text(f"DELETE FROM {table}"))
        db.commit()

    assert migrate_module.main() == 0
    assert customers() > 0


def test_waits_for_another_runner_instead_of_racing() -> None:
    holder = create_engine(get_settings().database_url, poolclass=NullPool)
    finished = threading.Event()
    with holder.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": migrate_module.MIGRATION_LOCK_ID})
        runner = threading.Thread(target=lambda: (migrate_module.main(), finished.set()), daemon=True)
        runner.start()
        # Longer than the app's 5 s statement timeout would allow, had it applied.
        assert not finished.wait(6)
        conn.execute(
            text("SELECT pg_advisory_unlock(:id)"), {"id": migrate_module.MIGRATION_LOCK_ID}
        )
        conn.commit()
    runner.join(timeout=30)
    assert finished.is_set()
    holder.dispose()


def test_failure_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("migration 42 failed")

    monkeypatch.setattr(migrate_module.command, "upgrade", broken)

    assert migrate_module.main() == 1


def test_prepares_a_brand_new_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real first start: no tables at all, so LangGraph's setup creates its indexes
    # (CREATE INDEX CONCURRENTLY), which must not wait on our own lock connection.
    test_url = make_url(get_settings().database_url)
    fresh = test_url.set(database=f"{test_url.database}_fresh")
    recreate_database(test_url, fresh.database or "")
    monkeypatch.setenv("DATABASE_URL", fresh.render_as_string(hide_password=False))
    reset_cached_db_objects()
    result: list[int] = []
    try:
        runner = threading.Thread(target=lambda: result.append(migrate_module.main()), daemon=True)
        runner.start()
        runner.join(timeout=60)
        assert result == [0], "migrate hung or failed on an empty database"
        engine = create_engine(fresh, poolclass=NullPool)
        tables = set(inspect(engine).get_table_names())
        assert LANGGRAPH_TABLES <= tables and {"customers", "test_runs"} <= tables
        assert customers() > 0
        engine.dispose()
    finally:
        monkeypatch.undo()
        reset_cached_db_objects()
