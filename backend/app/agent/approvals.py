"""Human-approval records as the agent graph uses them."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.state import ToolCallRecord
from app.db.lab_models import HumanApprovalRecord

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
NOT_RUN, EXECUTING, SUCCEEDED, FAILED = "not_run", "executing", "succeeded", "failed"


def get_or_create(
    db: Session, *, execution_id: str, customer_id: int, call: ToolCallRecord
) -> HumanApprovalRecord:
    """Idempotent: re-running the requesting step never creates a second approval."""
    existing = db.scalars(
        select(HumanApprovalRecord).where(
            HumanApprovalRecord.execution_id == execution_id,
            HumanApprovalRecord.tool_use_id == call["tool_use_id"],
        )
    ).one_or_none()
    if existing is not None:
        return existing

    details = (call["output"] or {}).get("details") or {}
    amount = Decimal(details["refund_amount"]) if "refund_amount" in details else None
    threshold = details.get("approval_threshold")
    approval = HumanApprovalRecord(
        execution_id=execution_id,
        tool_use_id=call["tool_use_id"],
        tool_name=call["name"],
        arguments=call["input"],
        customer_id=customer_id,
        order_id=details.get("order_id"),
        refund_amount=amount,
        # Every approval today comes from the refund threshold, which is the high-risk case.
        risk_level="high",
        reason=(
            f"Refund of ${amount:,.2f} is over the ${Decimal(threshold):,.2f} limit for "
            "automatic refunds."
            if amount is not None and threshold
            else (call["output"] or {}).get("message", "Needs human approval.")
        ),
        customer_reason=call["input"].get("reason"),
        status=PENDING,
        action_status=NOT_RUN,
    )
    db.add(approval)
    db.commit()
    return approval


def claim_action(db: Session, approval_id: int) -> bool:
    """Atomically move an approved action from not_run to executing. Only one caller can
    win, which is what makes the approved action run at most once."""
    claimed = db.execute(
        update(HumanApprovalRecord)
        .where(
            HumanApprovalRecord.id == approval_id,
            HumanApprovalRecord.status == APPROVED,
            HumanApprovalRecord.action_status == NOT_RUN,
        )
        .values(action_status=EXECUTING)
    )
    db.commit()
    return bool(claimed.rowcount)  # type: ignore[attr-defined]


def record_outcome(
    db: Session, approval_id: int, *, action_status: str | None, result: Any
) -> None:
    approval = db.get(HumanApprovalRecord, approval_id)
    assert approval is not None
    if action_status is not None:
        approval.action_status = action_status
    approval.action_result = result
    approval.resumed_at = datetime.now(UTC)
    db.commit()
