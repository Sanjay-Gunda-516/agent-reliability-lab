from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db.models import Order
from app.domain.enums import OrderStatus
from app.domain.policies import PolicyType
from app.domain.rules import CancellationBlocker, ReturnBlocker
from app.seed import ALICE, BEN, reset_demo_data
from app.tools import orders as tools
from app.tools.schemas import (
    CancelOrderResult,
    CreateReturnInput,
    CreateReturnResult,
    CustomerProfileResult,
    OrderListResult,
    OrderLookupInput,
    OrderResult,
    PolicyInput,
    RefundEligibilityResult,
    ToolContext,
    ToolErrorCode,
    ToolFailure,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
AS_ALICE = ToolContext(customer_id=ALICE)
AS_BEN = ToolContext(customer_id=BEN)


@pytest.fixture
def db(db_session: Session) -> Session:
    reset_demo_data(db_session, NOW)
    db_session.commit()
    return db_session


def lookup(order_id: int) -> OrderLookupInput:
    return OrderLookupInput(order_id=order_id)


def status_of(db: Session, order_id: int) -> OrderStatus:
    db.expire_all()
    order = db.get(Order, order_id)
    assert order is not None
    return order.status


class TestLookups:
    def test_get_customer_returns_only_the_session_customer(self, db: Session) -> None:
        result = tools.get_customer(db, AS_ALICE)

        assert isinstance(result, CustomerProfileResult)
        assert result.name == "Alice Nguyen"

    def test_list_orders_is_scoped_to_customer(self, db: Session) -> None:
        result = tools.list_orders(db, AS_BEN)

        assert isinstance(result, OrderListResult)
        assert {o.order_id for o in result.orders} == {9001, 9002, 9003, 9005}

    def test_get_order_includes_items_and_return_status(self, db: Session) -> None:
        result = tools.get_order(db, AS_BEN, lookup(9005))

        assert isinstance(result, OrderResult)
        assert result.order.items[0].product_name == "Travel Backpack"
        assert result.order.return_status == "requested"

    def test_unknown_order_is_not_found(self, db: Session) -> None:
        result = tools.get_order(db, AS_ALICE, lookup(1234))

        assert isinstance(result, ToolFailure)
        assert result.error == ToolErrorCode.ORDER_NOT_FOUND

    def test_another_customers_order_looks_exactly_like_a_missing_one(self, db: Session) -> None:
        someone_elses = tools.get_order(db, AS_ALICE, lookup(9001))
        missing = tools.get_order(db, AS_ALICE, lookup(9999))

        assert isinstance(someone_elses, ToolFailure)
        assert isinstance(missing, ToolFailure)
        assert someone_elses.error == missing.error == ToolErrorCode.ORDER_NOT_FOUND
        assert "Ben" not in someone_elses.message
        assert "Headphones" not in someone_elses.message


class TestCancelOrder:
    def test_cancels_eligible_order(self, db: Session) -> None:
        result = tools.cancel_order(db, AS_ALICE, lookup(4821), now=NOW)

        assert isinstance(result, CancelOrderResult)
        assert result.status == OrderStatus.CANCELLED
        assert result.refund_amount == Decimal("89.99")
        assert status_of(db, 4821) == OrderStatus.CANCELLED

    def test_refuses_shipped_order_and_leaves_it_untouched(self, db: Session) -> None:
        result = tools.cancel_order(db, AS_ALICE, lookup(8821), now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.error == ToolErrorCode.CANCELLATION_NOT_ALLOWED
        assert result.reason == CancellationBlocker.ALREADY_SHIPPED
        assert status_of(db, 8821) == OrderStatus.SHIPPED

    def test_refuses_unshipped_order_outside_window(self, db: Session) -> None:
        result = tools.cancel_order(db, AS_BEN, lookup(9002), now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.reason == CancellationBlocker.WINDOW_EXPIRED
        assert status_of(db, 9002) == OrderStatus.PENDING

    def test_second_cancel_reports_already_cancelled(self, db: Session) -> None:
        tools.cancel_order(db, AS_ALICE, lookup(4821), now=NOW)

        result = tools.cancel_order(db, AS_ALICE, lookup(4821), now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.reason == CancellationBlocker.ALREADY_CANCELLED

    def test_cannot_cancel_another_customers_order(self, db: Session) -> None:
        result = tools.cancel_order(db, AS_ALICE, lookup(9001), now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.error == ToolErrorCode.ORDER_NOT_FOUND
        assert status_of(db, 9001) == OrderStatus.PROCESSING


class TestRefundsAndReturns:
    def test_refund_eligibility_for_recent_delivery(self, db: Session) -> None:
        result = tools.check_refund_eligibility(db, AS_ALICE, lookup(5532), now=NOW)

        assert isinstance(result, RefundEligibilityResult)
        assert result.eligible
        assert result.refund_amount == Decimal("64.00")
        assert not result.requires_approval

    @pytest.mark.parametrize(
        ("order_id", "blocker"),
        [
            (6104, ReturnBlocker.WINDOW_EXPIRED),
            (7311, ReturnBlocker.ORDER_CANCELLED),
            (7425, ReturnBlocker.ALREADY_RETURNED),
            (4821, ReturnBlocker.NOT_DELIVERED),
        ],
    )
    def test_refund_ineligible_cases(
        self, db: Session, order_id: int, blocker: ReturnBlocker
    ) -> None:
        result = tools.check_refund_eligibility(db, AS_ALICE, lookup(order_id), now=NOW)

        assert isinstance(result, RefundEligibilityResult)
        assert not result.eligible
        assert result.reason == blocker

    def test_creates_return_for_eligible_order(self, db: Session) -> None:
        args = CreateReturnInput(order_id=5532, reason="Grinder is too loud")

        result = tools.create_return(db, AS_ALICE, args, now=NOW)

        assert isinstance(result, CreateReturnResult)
        assert result.refund_amount == Decimal("64.00")
        assert result.status == "requested"

    def test_only_one_return_per_order(self, db: Session) -> None:
        args = CreateReturnInput(order_id=5532, reason="Grinder is too loud")
        tools.create_return(db, AS_ALICE, args, now=NOW)

        result = tools.create_return(db, AS_ALICE, args, now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.reason == ReturnBlocker.RETURN_ALREADY_REQUESTED

    def test_high_value_refund_is_blocked_without_approval(self, db: Session) -> None:
        args = CreateReturnInput(order_id=7420, reason="Screen flickers")

        result = tools.create_return(db, AS_ALICE, args, now=NOW)

        assert isinstance(result, ToolFailure)
        assert result.error == ToolErrorCode.APPROVAL_REQUIRED
        order = tools.get_order(db, AS_ALICE, lookup(7420))
        assert isinstance(order, OrderResult)
        assert order.order.return_status is None

    def test_high_value_refund_proceeds_once_approved(self, db: Session) -> None:
        args = CreateReturnInput(order_id=7420, reason="Screen flickers")

        result = tools.create_return(db, AS_ALICE, args, approved=True, now=NOW)

        assert isinstance(result, CreateReturnResult)
        assert result.refund_amount == Decimal("1249.00")


def test_get_policy_returns_rule_based_text() -> None:
    result = tools.get_policy(PolicyInput(policy_type=PolicyType.RETURNS))

    assert "30 days" in result.text
