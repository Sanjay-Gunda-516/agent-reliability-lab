"""Tool input/output contracts.

Inputs are what the LLM is allowed to send. The customer is never an input: it comes
from the authenticated session (ToolContext), so a prompt can't redirect a tool to
another customer's data.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import OrderStatus, ReturnStatus
from app.domain.policies import PolicyType


@dataclass(frozen=True)
class ToolContext:
    customer_id: int
    # Set only by the approval flow after a recorded human decision, never from model output.
    approved: bool = False


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(ToolInput):
    pass


class OrderLookupInput(ToolInput):
    order_id: int = Field(gt=0, description="The order number, e.g. 4821 for order #4821.")


class CreateReturnInput(ToolInput):
    order_id: int = Field(gt=0, description="The order number to return.")
    reason: str = Field(
        min_length=3, max_length=500, description="The customer's reason for the return."
    )


class PolicyInput(ToolInput):
    policy_type: PolicyType


class ToolErrorCode(StrEnum):
    CUSTOMER_NOT_FOUND = "customer_not_found"
    ORDER_NOT_FOUND = "order_not_found"
    CANCELLATION_NOT_ALLOWED = "cancellation_not_allowed"
    RETURN_NOT_ALLOWED = "return_not_allowed"
    APPROVAL_REQUIRED = "approval_required"


class ToolFailure(BaseModel):
    ok: Literal[False] = False
    error: ToolErrorCode
    reason: str | None = None
    message: str
    details: dict[str, Any] | None = None


class ToolSuccess(BaseModel):
    ok: Literal[True] = True


class OrderItemView(BaseModel):
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderSummary(BaseModel):
    order_id: int
    status: OrderStatus
    total_amount: Decimal
    placed_at: datetime
    product_names: list[str]


class OrderDetails(BaseModel):
    order_id: int
    status: OrderStatus
    total_amount: Decimal
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    items: list[OrderItemView]
    return_status: ReturnStatus | None


class CustomerProfileResult(ToolSuccess):
    customer_id: int
    name: str
    email: str


class OrderResult(ToolSuccess):
    order: OrderDetails


class OrderListResult(ToolSuccess):
    orders: list[OrderSummary]


class CancelOrderResult(ToolSuccess):
    order_id: int
    status: OrderStatus
    refund_amount: Decimal


class RefundEligibilityResult(ToolSuccess):
    order_id: int
    eligible: bool
    reason: str | None
    refund_amount: Decimal
    requires_approval: bool


class CreateReturnResult(ToolSuccess):
    return_id: int
    order_id: int
    refund_amount: Decimal
    status: ReturnStatus


class PolicyResult(ToolSuccess):
    policy_type: PolicyType
    text: str
