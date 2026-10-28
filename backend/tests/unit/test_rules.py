from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.enums import OrderStatus
from app.domain.rules import (
    CANCELLATION_WINDOW,
    REFUND_APPROVAL_THRESHOLD,
    RETURN_WINDOW,
    CancellationBlocker,
    ReturnBlocker,
    ReturnDecision,
    evaluate_cancellation,
    evaluate_return,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


class TestCancellation:
    @pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.PROCESSING])
    def test_unshipped_order_inside_window_can_be_cancelled(self, status: OrderStatus) -> None:
        decision = evaluate_cancellation(status, NOW - timedelta(hours=2), NOW)

        assert decision.allowed
        assert decision.blocker is None

    @pytest.mark.parametrize(
        "status", [OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.RETURNED]
    )
    def test_shipped_orders_cannot_be_cancelled(self, status: OrderStatus) -> None:
        decision = evaluate_cancellation(status, NOW - timedelta(hours=2), NOW)

        assert not decision.allowed
        assert decision.blocker == CancellationBlocker.ALREADY_SHIPPED

    def test_cancelled_order_reports_already_cancelled(self) -> None:
        decision = evaluate_cancellation(OrderStatus.CANCELLED, NOW - timedelta(hours=2), NOW)

        assert decision.blocker == CancellationBlocker.ALREADY_CANCELLED

    def test_window_boundary_is_inclusive(self) -> None:
        assert evaluate_cancellation(OrderStatus.PROCESSING, NOW - CANCELLATION_WINDOW, NOW).allowed

    def test_unshipped_order_outside_window_cannot_be_cancelled(self) -> None:
        placed = NOW - CANCELLATION_WINDOW - timedelta(seconds=1)

        decision = evaluate_cancellation(OrderStatus.PENDING, placed, NOW)

        assert not decision.allowed
        assert decision.blocker == CancellationBlocker.WINDOW_EXPIRED

    def test_shipping_status_takes_priority_over_window(self) -> None:
        # A shipped, old order should say "shipped", which is the actionable reason.
        decision = evaluate_cancellation(OrderStatus.SHIPPED, NOW - timedelta(days=5), NOW)

        assert decision.blocker == CancellationBlocker.ALREADY_SHIPPED


class TestReturns:
    def evaluate(
        self,
        status: OrderStatus = OrderStatus.DELIVERED,
        delivered_ago: timedelta | None = timedelta(days=3),
        total: Decimal = Decimal("80.00"),
        has_existing_return: bool = False,
    ) -> ReturnDecision:
        delivered_at = NOW - delivered_ago if delivered_ago is not None else None
        return evaluate_return(status, delivered_at, total, has_existing_return, NOW)

    def test_recent_delivery_is_eligible_for_full_refund(self) -> None:
        decision = self.evaluate(total=Decimal("80.00"))

        assert decision.eligible
        assert decision.refund_amount == Decimal("80.00")
        assert not decision.requires_approval

    def test_refund_above_threshold_requires_approval(self) -> None:
        decision = self.evaluate(total=REFUND_APPROVAL_THRESHOLD + Decimal("0.01"))

        assert decision.eligible
        assert decision.requires_approval

    def test_refund_at_threshold_does_not_require_approval(self) -> None:
        assert not self.evaluate(total=REFUND_APPROVAL_THRESHOLD).requires_approval

    def test_return_window_boundary_is_inclusive(self) -> None:
        assert self.evaluate(delivered_ago=RETURN_WINDOW).eligible

    def test_return_window_expired(self) -> None:
        decision = self.evaluate(delivered_ago=RETURN_WINDOW + timedelta(seconds=1))

        assert not decision.eligible
        assert decision.blocker == ReturnBlocker.WINDOW_EXPIRED
        assert decision.refund_amount == 0

    @pytest.mark.parametrize(
        "status", [OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.SHIPPED]
    )
    def test_undelivered_orders_are_not_returnable(self, status: OrderStatus) -> None:
        decision = self.evaluate(status=status, delivered_ago=None)

        assert decision.blocker == ReturnBlocker.NOT_DELIVERED

    def test_cancelled_order_is_not_returnable(self) -> None:
        assert self.evaluate(status=OrderStatus.CANCELLED).blocker == ReturnBlocker.ORDER_CANCELLED

    def test_returned_order_is_not_returnable_again(self) -> None:
        assert self.evaluate(status=OrderStatus.RETURNED).blocker == ReturnBlocker.ALREADY_RETURNED

    def test_only_one_return_per_order(self) -> None:
        decision = self.evaluate(has_existing_return=True)

        assert decision.blocker == ReturnBlocker.RETURN_ALREADY_REQUESTED
