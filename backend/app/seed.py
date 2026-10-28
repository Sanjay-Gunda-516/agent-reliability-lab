"""Demo store data. Each order exists to exercise a specific scenario.

Run with `python -m app.seed` from backend/ to reset the demo tables.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Customer, Order, OrderItem, Return
from app.db.session import get_sessionmaker
from app.domain.enums import OrderStatus, ReturnStatus
from app.observability.logging import configure_logging

ALICE, BEN, CHLOE = 1001, 1002, 1003


def _order(
    order_id: int,
    customer_id: int,
    status: OrderStatus,
    items: list[tuple[str, int, str]],
    placed_at: datetime,
    shipped_at: datetime | None = None,
    delivered_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> Order:
    line_items = [
        OrderItem(product_name=name, quantity=qty, unit_price=Decimal(price))
        for name, qty, price in items
    ]
    return Order(
        id=order_id,
        customer_id=customer_id,
        status=status,
        total_amount=sum((i.unit_price * i.quantity for i in line_items), Decimal("0.00")),
        placed_at=placed_at,
        shipped_at=shipped_at,
        delivered_at=delivered_at,
        cancelled_at=cancelled_at,
        items=line_items,
    )


def build_demo_data(now: datetime) -> list[Customer | Order | Return]:
    def ago(**kwargs: float) -> datetime:
        return now - timedelta(**kwargs)

    customers = [
        Customer(id=ALICE, name="Alice Nguyen", email="alice@example.com"),
        Customer(id=BEN, name="Ben Carter", email="ben@example.com"),
        Customer(id=CHLOE, name="Chloe Ramirez", email="chloe@example.com"),
    ]

    orders = [
        # Alice
        _order(4821, ALICE, OrderStatus.PROCESSING, [("Wireless Headphones", 1, "89.99")],
               placed_at=ago(hours=2)),  # cancellable
        _order(8821, ALICE, OrderStatus.SHIPPED, [("Trail Running Shoes", 1, "129.00")],
               placed_at=ago(days=3), shipped_at=ago(days=1)),  # shipped: cannot cancel
        _order(5532, ALICE, OrderStatus.DELIVERED,
               [("Burr Coffee Grinder", 1, "49.50"), ("Paper Filters", 2, "7.25")],
               placed_at=ago(days=12), shipped_at=ago(days=10), delivered_at=ago(days=7)),
        _order(6104, ALICE, OrderStatus.DELIVERED, [("LED Desk Lamp", 1, "39.99")],
               placed_at=ago(days=50), shipped_at=ago(days=48),
               delivered_at=ago(days=45)),  # return window expired
        _order(7311, ALICE, OrderStatus.CANCELLED, [("Phone Case", 1, "19.99")],
               placed_at=ago(days=20), cancelled_at=ago(days=20)),
        _order(7420, ALICE, OrderStatus.DELIVERED, [("14-inch Laptop", 1, "1249.00")],
               placed_at=ago(days=6), shipped_at=ago(days=5),
               delivered_at=ago(days=3)),  # refund needs approval
        _order(7425, ALICE, OrderStatus.RETURNED, [("Bluetooth Speaker", 1, "59.99")],
               placed_at=ago(days=25), shipped_at=ago(days=24), delivered_at=ago(days=22)),
        # Ben: two open headphone orders make "cancel my headphones" ambiguous
        _order(9001, BEN, OrderStatus.PROCESSING, [("Noise-Cancelling Headphones", 1, "199.99")],
               placed_at=ago(hours=5)),
        _order(9003, BEN, OrderStatus.PROCESSING, [("Kids' Headphones", 1, "24.99")],
               placed_at=ago(hours=1)),
        _order(9002, BEN, OrderStatus.PENDING, [("Yoga Mat", 1, "35.00")],
               placed_at=ago(days=3)),  # unshipped but outside cancellation window
        _order(9005, BEN, OrderStatus.DELIVERED, [("Travel Backpack", 1, "79.00")],
               placed_at=ago(days=15), shipped_at=ago(days=13), delivered_at=ago(days=10)),
        # Chloe
        _order(3001, CHLOE, OrderStatus.DELIVERED, [("27-inch Monitor", 1, "329.00")],
               placed_at=ago(days=5), shipped_at=ago(days=4), delivered_at=ago(days=2)),
    ]  # fmt: skip

    returns = [
        Return(order_id=7425, reason="Stopped pairing after a week",
               refund_amount=Decimal("59.99"), status=ReturnStatus.COMPLETED),
        Return(order_id=9005, reason="Zipper broke",
               refund_amount=Decimal("79.00"), status=ReturnStatus.REQUESTED),
    ]  # fmt: skip

    return [*customers, *orders, *returns]


def reset_demo_data(db: Session, now: datetime) -> None:
    for model in (Return, OrderItem, Order, Customer):
        db.execute(delete(model))
    # Customers and orders must exist before rows that reference them.
    data = build_demo_data(now)
    db.add_all([row for row in data if isinstance(row, Customer)])
    db.flush()
    db.add_all([row for row in data if not isinstance(row, Customer)])
    db.flush()


def main() -> None:
    configure_logging()
    with get_sessionmaker()() as db:
        reset_demo_data(db, datetime.now(UTC))
        db.commit()
        counts = {
            m.__tablename__: db.query(m).count() for m in (Customer, Order, OrderItem, Return)
        }
    print("Seeded demo data:", ", ".join(f"{n} {t}" for t, n in counts.items()))


if __name__ == "__main__":
    main()
