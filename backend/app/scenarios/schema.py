"""Scenario definitions: a test conversation and what *should* happen in it.

Ground truth is written by hand from the store's published policy. Nothing in this
package may compute expectations with the agent's own rule code (app.domain.rules or
app.tools); otherwise a bug in a rule would also be baked into the answer key.
"""

import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from app.agent.state import EscalationReason, Intent
from app.agent.tool_registry import TOOLS
from app.domain.enums import OrderStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tag(StrEnum):
    HAPPY_PATH = "happy-path"
    EDGE_CASE = "edge-case"
    MISSING_INFO = "missing-info"
    MULTI_TURN = "multi-turn"
    ADVERSARIAL = "adversarial"
    PRIVACY = "privacy"
    POLICY = "policy"
    FAILURE_RECOVERY = "failure-recovery"
    ESCALATION = "escalation"


class Persona(StrEnum):
    NORMAL = "normal"
    ANGRY = "angry"
    INCOMPLETE = "incomplete"
    CONFUSED = "confused"
    ADVERSARIAL = "adversarial"


class Outcome(StrEnum):
    ORDER_CANCELLED = "order_cancelled"
    CANCELLATION_REFUSED = "cancellation_refused"
    RETURN_CREATED = "return_created"
    RETURN_REFUSED = "return_refused"
    APPROVAL_ESCALATED = "approval_escalated"
    INFORMATION_PROVIDED = "information_provided"
    CLARIFICATION_REQUESTED = "clarification_requested"
    NOT_FOUND_REPORTED = "not_found_reported"
    REQUEST_DECLINED = "request_declined"
    GRACEFUL_FAILURE = "graceful_failure"


class FaultType(StrEnum):
    TOOL_TIMEOUT = "tool_timeout"
    DATABASE_ERROR = "database_error"
    TOOL_CRASH = "tool_crash"
    LLM_UNAVAILABLE = "llm_unavailable"


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def _parse_duration(value: Any) -> Any:
    if isinstance(value, str):
        match = _DURATION.match(value.strip())
        if not match:
            raise ValueError(f"expected a duration like '30m', '2h' or '3d', got {value!r}")
        amount, unit = match.groups()
        return timedelta(**{_UNITS[unit]: float(amount)})
    return value


Duration = Annotated[timedelta, BeforeValidator(_parse_duration)]


def _check_tool(name: str, args: dict[str, Any] | None) -> None:
    spec = TOOLS.get(name)
    if spec is None:
        raise ValueError(f"unknown tool {name!r}")
    unknown = set(args or {}) - set(spec.input_model.model_fields)
    if unknown:
        raise ValueError(f"{name} has no argument(s) {sorted(unknown)}")


# --- Setup ---------------------------------------------------------------------------


class ItemFixture(StrictModel):
    product_name: str
    quantity: int = Field(default=1, gt=0)
    unit_price: Decimal = Field(ge=0)


class OrderFixture(StrictModel):
    """An order added to (or replacing one in) the demo store for this scenario."""

    id: int = Field(gt=0)
    customer_id: int = Field(gt=0)
    status: OrderStatus
    items: list[ItemFixture] = Field(min_length=1)
    placed_ago: Duration
    shipped_ago: Duration | None = None
    delivered_ago: Duration | None = None
    cancelled_ago: Duration | None = None


class Setup(StrictModel):
    orders: list[OrderFixture] = Field(default_factory=list)


# --- Ground truth --------------------------------------------------------------------


class RequiredToolCall(StrictModel):
    """A call the agent must make. `args` is a subset match."""

    tool: str
    args: dict[str, Any] | None = None
    result: Literal["success", "rejected", "any"] = "success"

    @model_validator(mode="after")
    def _known_tool(self) -> Self:
        _check_tool(self.tool, self.args)
        return self


class ForbiddenToolCall(StrictModel):
    """A call the agent must not make. With result=success, attempting it is tolerated
    as long as it doesn't go through."""

    tool: str
    args: dict[str, Any] | None = None
    result: Literal["success", "any"] = "any"

    @model_validator(mode="after")
    def _known_tool(self) -> Self:
        _check_tool(self.tool, self.args)
        return self


class StateExpectation(StrictModel):
    orders: dict[int, OrderStatus] = Field(default_factory=dict)
    # order id -> whether a return exists for it after the conversation
    returns: dict[int, bool] = Field(default_factory=dict)


class ResponseExpectation(StrictModel):
    """Case-insensitive substring checks on the final reply. Use sparingly: facts that
    must appear (an order number) or must never leak (another customer's product)."""

    must_mention: list[str] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)


class Expected(StrictModel):
    behavior: str = Field(min_length=10, description="What a correct agent does, in words.")
    outcome: Outcome
    final_status: Literal["completed", "escalated"]
    escalation_reason: EscalationReason | None = None
    intent: Intent | None = Field(default=None, description="Intent of the first turn.")
    must_call: list[RequiredToolCall] = Field(default_factory=list)
    must_not_call: list[ForbiddenToolCall] = Field(default_factory=list)
    state: StateExpectation = Field(default_factory=StateExpectation)
    response: ResponseExpectation = Field(default_factory=ResponseExpectation)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.final_status == "escalated") != (self.escalation_reason is not None):
            raise ValueError("escalation_reason must be set exactly when final_status is escalated")
        if (
            self.outcome == Outcome.APPROVAL_ESCALATED
            and self.escalation_reason != EscalationReason.APPROVAL_REQUIRED
        ):
            raise ValueError("approval_escalated requires escalation_reason approval_required")
        if self.outcome == Outcome.ORDER_CANCELLED and OrderStatus.CANCELLED not in set(
            self.state.orders.values()
        ):
            raise ValueError("order_cancelled must name the cancelled order in state.orders")
        if self.outcome == Outcome.RETURN_CREATED and True not in set(self.state.returns.values()):
            raise ValueError("return_created must name the returned order in state.returns")
        return self


# --- Faults --------------------------------------------------------------------------


class FaultSpec(StrictModel):
    type: FaultType
    tool: str | None = None
    times: int | None = Field(
        default=None, gt=0, description="Failures before recovery; None = always."
    )

    @model_validator(mode="after")
    def _target(self) -> Self:
        if self.type == FaultType.LLM_UNAVAILABLE:
            if self.tool is not None:
                raise ValueError("llm_unavailable faults don't target a tool")
        elif self.tool is None:
            raise ValueError(f"{self.type} faults need a tool")
        else:
            _check_tool(self.tool, None)
        return self


# --- Scenario ------------------------------------------------------------------------


class Scenario(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=80)
    title: str
    tags: list[Tag] = Field(min_length=1)
    customer_id: int = Field(gt=0, description="The signed-in customer.")
    setup: Setup = Field(default_factory=Setup)
    faults: list[FaultSpec] = Field(default_factory=list)
    # Persona -> the customer's message(s). Variants only change wording, never facts,
    # so they all share one ground truth.
    variants: dict[Persona, list[str]] = Field(min_length=1)
    expected: Expected

    @field_validator("variants", mode="before")
    @classmethod
    def _single_turn_shorthand(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: [v] if isinstance(v, str) else v for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        for persona, turns in self.variants.items():
            if not turns or any(not turn.strip() for turn in turns):
                raise ValueError(f"variant {persona!r} needs non-empty customer messages")
        multi_turn = any(len(turns) > 1 for turns in self.variants.values())
        if multi_turn != (Tag.MULTI_TURN in self.tags):
            raise ValueError("tag multi-turn exactly when a variant has more than one turn")
        if self.faults and Tag.FAILURE_RECOVERY not in self.tags:
            raise ValueError("scenarios with faults must be tagged failure-recovery")
        return self

    def content_hash(self) -> str:
        """Changes whenever anything that affects the test changes."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()


def variant_id(scenario_id: str, persona: Persona) -> str:
    return f"{scenario_id}:{persona}"
