"""Deterministic business rules. These decide; the LLM only explains the outcome."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.domain.enums import OrderStatus

CANCELLATION_WINDOW = timedelta(hours=24)
RETURN_WINDOW = timedelta(days=30)
REFUND_APPROVAL_THRESHOLD = Decimal("500.00")

CANCELLABLE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.PROCESSING})


class CancellationBlocker(StrEnum):
    ALREADY_CANCELLED = "already_cancelled"
    ALREADY_SHIPPED = "already_shipped"
    WINDOW_EXPIRED = "cancellation_window_expired"


class ReturnBlocker(StrEnum):
    ORDER_CANCELLED = "order_cancelled"
    ALREADY_RETURNED = "already_returned"
    NOT_DELIVERED = "not_delivered"
    RETURN_ALREADY_REQUESTED = "return_already_requested"
    WINDOW_EXPIRED = "return_window_expired"


@dataclass(frozen=True)
class CancellationDecision:
    allowed: bool
    blocker: CancellationBlocker | None = None


@dataclass(frozen=True)
class ReturnDecision:
    eligible: bool
    blocker: ReturnBlocker | None = None
    refund_amount: Decimal = Decimal("0.00")
    requires_approval: bool = False


def evaluate_cancellation(
    status: OrderStatus, placed_at: datetime, now: datetime
) -> CancellationDecision:
    if status == OrderStatus.CANCELLED:
        return CancellationDecision(False, CancellationBlocker.ALREADY_CANCELLED)
    if status not in CANCELLABLE_STATUSES:
        return CancellationDecision(False, CancellationBlocker.ALREADY_SHIPPED)
    if now - placed_at > CANCELLATION_WINDOW:
        return CancellationDecision(False, CancellationBlocker.WINDOW_EXPIRED)
    return CancellationDecision(True)


def evaluate_return(
    status: OrderStatus,
    delivered_at: datetime | None,
    order_total: Decimal,
    has_existing_return: bool,
    now: datetime,
) -> ReturnDecision:
    """Refunds are issued through returns, so this also answers refund eligibility."""
    if status == OrderStatus.CANCELLED:
        return ReturnDecision(False, ReturnBlocker.ORDER_CANCELLED)
    if status == OrderStatus.RETURNED:
        return ReturnDecision(False, ReturnBlocker.ALREADY_RETURNED)
    if status != OrderStatus.DELIVERED or delivered_at is None:
        return ReturnDecision(False, ReturnBlocker.NOT_DELIVERED)
    if has_existing_return:
        return ReturnDecision(False, ReturnBlocker.RETURN_ALREADY_REQUESTED)
    if now - delivered_at > RETURN_WINDOW:
        return ReturnDecision(False, ReturnBlocker.WINDOW_EXPIRED)
    return ReturnDecision(
        True,
        refund_amount=order_total,
        requires_approval=order_total > REFUND_APPROVAL_THRESHOLD,
    )
