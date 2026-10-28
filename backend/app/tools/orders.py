"""Customer-support tools: the only code path through which the agent reads or changes
store data. Business rules are applied here, never left to the model."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Customer, Order, Return
from app.domain.enums import OrderStatus, ReturnStatus
from app.domain.policies import get_policy_text
from app.domain.rules import REFUND_APPROVAL_THRESHOLD, evaluate_cancellation, evaluate_return
from app.tools.schemas import (
    CancelOrderResult,
    CreateReturnInput,
    CreateReturnResult,
    CustomerProfileResult,
    OrderDetails,
    OrderItemView,
    OrderListResult,
    OrderLookupInput,
    OrderResult,
    OrderSummary,
    PolicyInput,
    PolicyResult,
    RefundEligibilityResult,
    ToolContext,
    ToolErrorCode,
    ToolFailure,
)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _find_order(
    db: Session, ctx: ToolContext, order_id: int, *, for_update: bool = False
) -> Order | None:
    stmt = select(Order).where(Order.id == order_id, Order.customer_id == ctx.customer_id)
    if for_update:
        # Serialize concurrent actions on the same order (e.g. a double-clicked cancel).
        stmt = stmt.with_for_update()
    return db.scalars(stmt).one_or_none()


def _order_not_found(order_id: int) -> ToolFailure:
    # Same answer for "doesn't exist" and "belongs to someone else", so order numbers
    # can't be probed to discover other customers' orders.
    return ToolFailure(
        error=ToolErrorCode.ORDER_NOT_FOUND,
        message=f"No order #{order_id} was found on this customer's account.",
    )


def _details(order: Order) -> OrderDetails:
    return OrderDetails(
        order_id=order.id,
        status=order.status,
        total_amount=order.total_amount,
        placed_at=order.placed_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        cancelled_at=order.cancelled_at,
        items=[
            OrderItemView(product_name=i.product_name, quantity=i.quantity, unit_price=i.unit_price)
            for i in order.items
        ],
        return_status=order.return_request.status if order.return_request else None,
    )


def get_customer(db: Session, ctx: ToolContext) -> CustomerProfileResult | ToolFailure:
    customer = db.get(Customer, ctx.customer_id)
    if customer is None:
        return ToolFailure(
            error=ToolErrorCode.CUSTOMER_NOT_FOUND, message="Customer account not found."
        )
    return CustomerProfileResult(customer_id=customer.id, name=customer.name, email=customer.email)


def list_orders(db: Session, ctx: ToolContext) -> OrderListResult:
    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == ctx.customer_id)
        .options(selectinload(Order.items))
        .order_by(Order.placed_at.desc())
    )
    return OrderListResult(
        orders=[
            OrderSummary(
                order_id=o.id,
                status=o.status,
                total_amount=o.total_amount,
                placed_at=o.placed_at,
                product_names=[i.product_name for i in o.items],
            )
            for o in orders
        ]
    )


def get_order(db: Session, ctx: ToolContext, args: OrderLookupInput) -> OrderResult | ToolFailure:
    order = _find_order(db, ctx, args.order_id)
    if order is None:
        return _order_not_found(args.order_id)
    return OrderResult(order=_details(order))


def cancel_order(
    db: Session, ctx: ToolContext, args: OrderLookupInput, now: datetime | None = None
) -> CancelOrderResult | ToolFailure:
    now = _now(now)
    order = _find_order(db, ctx, args.order_id, for_update=True)
    if order is None:
        return _order_not_found(args.order_id)

    decision = evaluate_cancellation(order.status, order.placed_at, now)
    if not decision.allowed:
        db.rollback()  # release the row lock
        return ToolFailure(
            error=ToolErrorCode.CANCELLATION_NOT_ALLOWED,
            reason=decision.blocker,
            message=f"Order #{order.id} cannot be cancelled ({decision.blocker}).",
        )

    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now
    db.commit()
    return CancelOrderResult(
        order_id=order.id, status=order.status, refund_amount=order.total_amount
    )


def check_refund_eligibility(
    db: Session, ctx: ToolContext, args: OrderLookupInput, now: datetime | None = None
) -> RefundEligibilityResult | ToolFailure:
    order = _find_order(db, ctx, args.order_id)
    if order is None:
        return _order_not_found(args.order_id)

    decision = evaluate_return(
        order.status,
        order.delivered_at,
        order.total_amount,
        order.return_request is not None,
        _now(now),
    )
    return RefundEligibilityResult(
        order_id=order.id,
        eligible=decision.eligible,
        reason=decision.blocker,
        refund_amount=decision.refund_amount,
        requires_approval=decision.requires_approval,
    )


def create_return(
    db: Session,
    ctx: ToolContext,
    args: CreateReturnInput,
    *,
    approved: bool = False,
    now: datetime | None = None,
) -> CreateReturnResult | ToolFailure:
    """`approved` is set only by the human-approval flow, never from model output."""
    order = _find_order(db, ctx, args.order_id, for_update=True)
    if order is None:
        return _order_not_found(args.order_id)

    decision = evaluate_return(
        order.status,
        order.delivered_at,
        order.total_amount,
        order.return_request is not None,
        _now(now),
    )
    if not decision.eligible:
        db.rollback()
        return ToolFailure(
            error=ToolErrorCode.RETURN_NOT_ALLOWED,
            reason=decision.blocker,
            message=f"Order #{order.id} is not eligible for a return ({decision.blocker}).",
        )
    if decision.requires_approval and not approved:
        db.rollback()
        return ToolFailure(
            error=ToolErrorCode.APPROVAL_REQUIRED,
            message=(
                f"A refund of ${decision.refund_amount:,.2f} for order #{order.id} "
                "needs human approval before the return can be created."
            ),
            details={
                "order_id": order.id,
                "refund_amount": str(decision.refund_amount),
                "approval_threshold": str(REFUND_APPROVAL_THRESHOLD),
            },
        )

    return_request = Return(
        order=order,
        reason=args.reason,
        refund_amount=decision.refund_amount,
        status=ReturnStatus.REQUESTED,
    )
    db.add(return_request)
    db.commit()
    return CreateReturnResult(
        return_id=return_request.id,
        order_id=order.id,
        refund_amount=return_request.refund_amount,
        status=return_request.status,
    )


def get_policy(args: PolicyInput) -> PolicyResult:
    return PolicyResult(policy_type=args.policy_type, text=get_policy_text(args.policy_type))
