"""app.migrate: schema, checkpoint tables and demo seed, once, under a lock."""

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.pool import NullPool

from app import migrate as migrate_module
from app.config import get_settings
from app.db.models import Customer, Order
from app.db.session import get_sessionmaker
from app.seed import reset_demo_data

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
        runner = threading.Thread(target=lambda: (migrate_module.main(), finished.set()))
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
