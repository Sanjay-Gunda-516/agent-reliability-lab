import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


class Intent(StrEnum):
    ORDER_STATUS = "order_status"
    CANCEL_ORDER = "cancel_order"
    RETURN_REQUEST = "return_request"
    REFUND_INQUIRY = "refund_inquiry"
    POLICY_QUESTION = "policy_question"
    ACCOUNT_INFO = "account_info"
    OTHER = "other"


class IntentResult(BaseModel):
    intent: Intent
    order_ids: list[int] = Field(
        default_factory=list, description="Order numbers explicitly mentioned by the customer."
    )


class EscalationReason(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    LLM_UNAVAILABLE = "llm_unavailable"
    MODEL_REFUSAL = "model_refusal"
    STEP_LIMIT = "step_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    RESPONSE_TRUNCATED = "response_truncated"
    EMPTY_RESPONSE = "empty_response"


class LLMCallRecord(TypedDict):
    purpose: Literal["intent", "agent"]
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    latency_ms: float
    error: str | None


ToolCallStatus = Literal["success", "rejected", "error"]


class ToolCallRecord(TypedDict):
    tool_use_id: str
    name: str
    input: dict[str, Any]
    # success: tool ran and did what was asked; rejected: a business rule refused it;
    # error: invalid arguments, unknown tool, repeated call or an infrastructure failure.
    status: ToolCallStatus
    output: dict[str, Any] | None
    error: str | None
    attempts: int
    latency_ms: float


class AgentState(TypedDict, total=False):
    # Anthropic Messages API format. Append-only so history is never rewritten.
    messages: Annotated[list[dict[str, Any]], operator.add]
    intent: dict[str, Any] | None
    llm_calls: Annotated[list[LLMCallRecord], operator.add]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    status: Literal["completed", "escalated"] | None
    escalation_reason: EscalationReason | None
    final_response: str | None
