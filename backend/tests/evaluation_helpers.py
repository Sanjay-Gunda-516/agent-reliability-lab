"""Builders for scenario runs, so checks can be tested without running the agent."""

import copy
from datetime import UTC, datetime
from typing import Any

from app.agent.state import LLMCallRecord, ToolCallRecord
from app.domain.enums import OrderStatus, ReturnStatus
from app.scenarios.faults import InjectedFault
from app.scenarios.runner import ScenarioRun, StateSnapshot, TurnResult
from app.scenarios.schema import Persona, Scenario

OWNERS = {4821: 1001, 8821: 1001, 5532: 1001, 9001: 1002}
BASE_ORDERS = {
    4821: OrderStatus.PROCESSING,
    8821: OrderStatus.SHIPPED,
    5532: OrderStatus.DELIVERED,
    9001: OrderStatus.PROCESSING,
}


def make_scenario(**overrides: Any) -> Scenario:
    data: dict[str, Any] = {
        "id": "test-scenario",
        "title": "Test scenario",
        "tags": ["happy-path"],
        "customer_id": 1001,
        "variants": {"normal": "Please cancel order #4821."},
        "expected": {
            "behavior": "Cancels order 4821 and confirms it.",
            "outcome": "order_cancelled",
            "final_status": "completed",
            "intent": "cancel_order",
            "must_call": [{"tool": "cancel_order", "args": {"order_id": 4821}}],
            "state": {"orders": {4821: "cancelled"}},
        },
    }
    for key, value in overrides.items():
        if key in data["expected"] or key in {
            "must_not_call", "response", "escalation_reason",
        }:  # fmt: skip
            data["expected"][key] = value
        else:
            data[key] = value
    return Scenario.model_validate(data)


def tool_call(
    name: str,
    tool_input: dict[str, Any] | None = None,
    status: str = "success",
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(
        tool_use_id=f"toolu_{name}",
        name=name,
        input=tool_input or {},
        status=status,  # type: ignore[typeddict-item]
        output=output if output is not None else ({"ok": True} if status == "success" else None),
        error=error,
        attempts=1,
        latency_ms=5.0,
    )


def llm_call(
    purpose: str = "agent", input_tokens: int = 1000, output_tokens: int = 100
) -> LLMCallRecord:
    return LLMCallRecord(
        purpose=purpose,  # type: ignore[typeddict-item]
        model="claude-opus-5",
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        latency_ms=800.0,
        error=None,
    )


def turn(
    reply: str = "Done.",
    status: str = "completed",
    tools: list[ToolCallRecord] | None = None,
    intent: str | None = "cancel_order",
    escalation_reason: str | None = None,
    llm_calls: list[LLMCallRecord] | None = None,
) -> TurnResult:
    return TurnResult(
        message="Please cancel order #4821.",
        status=status,
        final_response=reply,
        escalation_reason=escalation_reason,
        intent={"intent": intent, "order_ids": []} if intent else None,
        tool_calls=tools or [],
        llm_calls=llm_calls if llm_calls is not None else [llm_call("intent"), llm_call()],
        latency_ms=1500.0,
    )


def snapshot(
    orders: dict[int, OrderStatus] | None = None,
    returns: dict[int, ReturnStatus] | None = None,
) -> StateSnapshot:
    merged = copy.copy(BASE_ORDERS)
    merged.update(orders or {})
    return StateSnapshot(orders=merged, returns=returns or {}, owners=OWNERS)


def make_run(
    scenario: Scenario,
    turns: list[TurnResult] | None = None,
    final: StateSnapshot | None = None,
    initial: StateSnapshot | None = None,
    faults: list[InjectedFault] | None = None,
    error: str | None = None,
    persona: Persona = Persona.NORMAL,
) -> ScenarioRun:
    return ScenarioRun(
        scenario_id=scenario.id,
        persona=persona,
        scenario_hash=scenario.content_hash(),
        customer_id=scenario.customer_id,
        started_at=datetime(2026, 1, 15, tzinfo=UTC),
        latency_ms=1600.0,
        turns=turns if turns is not None else [turn()],
        initial_state=None if error else (initial or snapshot()),
        final_state=None if error else (final or snapshot()),
        injected_faults=faults or [],
        error=error,
    )


def correct_cancel_run(scenario: Scenario) -> ScenarioRun:
    return make_run(
        scenario,
        turns=[
            turn("Order #4821 is cancelled.", tools=[tool_call("cancel_order", {"order_id": 4821})])
        ],
        final=snapshot({4821: OrderStatus.CANCELLED}),
    )
