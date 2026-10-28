from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Customer, Order, OrderItem, Return
from app.seed import reset_demo_data

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_seed_is_repeatable(db_session: Session) -> None:
    reset_demo_data(db_session, NOW)
    first = [count(db_session, m) for m in (Customer, Order, OrderItem, Return)]

    reset_demo_data(db_session, NOW)
    second = [count(db_session, m) for m in (Customer, Order, OrderItem, Return)]

    assert first == second == [3, 12, 13, 2]


def test_order_totals_match_line_items(db_session: Session) -> None:
    reset_demo_data(db_session, NOW)

    for order in db_session.scalars(select(Order)):
        assert order.total_amount == sum(i.unit_price * i.quantity for i in order.items)


def test_example_order_from_the_design_doc_exists(db_session: Session) -> None:
    reset_demo_data(db_session, NOW)

    order = db_session.get(Order, 4821)

    assert order is not None
    assert order.status == "processing"
    assert order.customer.name == "Alice Nguyen"
