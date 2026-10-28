from enum import StrEnum

from app.domain.rules import CANCELLATION_WINDOW, REFUND_APPROVAL_THRESHOLD, RETURN_WINDOW


class PolicyType(StrEnum):
    CANCELLATION = "cancellation"
    RETURNS = "returns"
    REFUNDS = "refunds"


# Built from the rule constants so the policy the agent quotes can't drift from
# what the rules actually enforce.
POLICIES: dict[PolicyType, str] = {
    PolicyType.CANCELLATION: (
        "Orders can be cancelled within "
        f"{int(CANCELLATION_WINDOW.total_seconds() // 3600)} hours of being placed, "
        "as long as they have not shipped yet. Shipped orders cannot be cancelled; "
        "once delivered they can be returned instead. Cancelled orders are refunded "
        "in full to the original payment method."
    ),
    PolicyType.RETURNS: (
        f"Delivered orders can be returned within {RETURN_WINDOW.days} days of delivery. "
        "Each order can be returned once. Orders that were cancelled or already "
        "returned are not eligible."
    ),
    PolicyType.REFUNDS: (
        "Refunds for delivered orders are issued through a return and cover the full "
        f"order total. Refunds over ${REFUND_APPROVAL_THRESHOLD:,.2f} are reviewed by a "
        "member of our support team before they are processed."
    ),
}


def get_policy_text(policy: PolicyType) -> str:
    return POLICIES[policy]
