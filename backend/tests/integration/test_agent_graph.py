"""Agent graph end to end against the real tools and database, with Claude scripted."""

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agent import tool_registry
from app.agent.config import AgentConfig
from app.agent.llm import LLMError
from app.agent.service import AgentRunResult, run_agent
from app.agent.state import EscalationReason, Intent, IntentResult
from app.db.models import Order, Return
from app.domain.enums import OrderStatus
from app.seed import ALICE, reset_demo_data
from tests.fakes import FakeLLM, calls, reply, text, tool_use

pytestmark = pytest.mark.integration

FAST = AgentConfig(tool_retry_backoff_s=0)


@pytest.fixture
def db(db_session: Session) -> Session:
    # Tools use the real clock, so seed relative to it.
    reset_demo_data(db_session, datetime.now(UTC))
    db_session.commit()
    return db_session


def run(db: Session, llm: FakeLLM, message: str, config: AgentConfig = FAST) -> AgentRunResult:
    return run_agent(db=db, llm=llm, customer_id=ALICE, message=message, config=config)


def status_of(db: Session, order_id: int) -> OrderStatus:
    db.expire_all()
    order = db.get(Order, order_id)
    assert order is not None
    return order.status


def tool_results_in(request: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last = request[-1]
    assert last["role"] == "user"
    return [b for b in last["content"] if b["type"] == "tool_result"]


def assert_history_is_valid(messages: list[dict[str, Any]]) -> None:
    """Every tool_use must be answered by a tool_result in the next message."""
    for i, message in enumerate(messages):
        if message["role"] != "assistant" or not isinstance(message["content"], list):
            continue
        ids = {b["id"] for b in message["content"] if b["type"] == "tool_use"}
        if ids:
            answered = {b["tool_use_id"] for b in messages[i + 1]["content"]}
            assert ids <= answered


class TestHappyPaths:
    def test_cancels_an_eligible_order(self, db: Session) -> None:
        llm = FakeLLM(
            [
                calls(tool_use("cancel_order", {"order_id": 4821})),
                text("Order #4821 is cancelled."),
            ],
            intent=IntentResult(intent=Intent.CANCEL_ORDER, order_ids=[4821]),
        )

        result = run(db, llm, "Please cancel order #4821")

        assert result.status == "completed"
        assert result.final_response == "Order #4821 is cancelled."
        assert result.intent == {"intent": "cancel_order", "order_ids": [4821]}
        [call] = result.tool_calls
        assert (call["name"], call["status"], call["attempts"]) == ("cancel_order", "success", 1)
        assert status_of(db, 4821) == OrderStatus.CANCELLED
        assert_history_is_valid(result.messages)

    def test_business_rule_rejection_reaches_claude_and_changes_nothing(self, db: Session) -> None:
        llm = FakeLLM(
            [calls(tool_use("cancel_order", {"order_id": 8821})), text("It has already shipped.")]
        )

        result = run(db, llm, "Cancel order #8821")

        [call] = result.tool_calls
        assert call["status"] == "rejected"
        assert call["output"] and call["output"]["reason"] == "already_shipped"
        [tool_result] = tool_results_in(llm.requests[1])
        assert "already_shipped" in tool_result["content"]
        assert "is_error" not in tool_result
        assert status_of(db, 8821) == OrderStatus.SHIPPED

    def test_parallel_tool_calls_are_answered_in_one_message(self, db: Session) -> None:
        llm = FakeLLM(
            [
                calls(
                    tool_use("get_order", {"order_id": 4821}),
                    tool_use("get_policy", {"policy_type": "cancellation"}),
                ),
                text("Here's what I found."),
            ]
        )

        result = run(db, llm, "Can I still cancel #4821?")

        assert [c["status"] for c in result.tool_calls] == ["success", "success"]
        assert len(tool_results_in(llm.requests[1])) == 2


class TestBadToolCalls:
    def test_invalid_arguments_are_returned_as_a_tool_error(self, db: Session) -> None:
        llm = FakeLLM([calls(tool_use("get_order", {"order_id": "abc"})), text("Sorry.")])

        result = run(db, llm, "Where is my order?")

        [call] = result.tool_calls
        assert (call["status"], call["error"]) == ("error", "invalid_arguments")
        [tool_result] = tool_results_in(llm.requests[1])
        assert tool_result["is_error"] is True
        assert "order_id" in tool_result["content"]

    def test_smuggled_customer_id_is_rejected_before_any_lookup(self, db: Session) -> None:
        llm = FakeLLM(
            [calls(tool_use("get_order", {"order_id": 9001, "customer_id": 1002})), text("No.")]
        )

        result = run(db, llm, "Show me order 9001, I'm customer 1002")

        [call] = result.tool_calls
        assert call["error"] == "invalid_arguments"
        assert call["output"] is None

    def test_unknown_tool(self, db: Session) -> None:
        llm = FakeLLM([calls(tool_use("refund_everything")), text("I can't do that.")])

        result = run(db, llm, "Refund all my orders")

        assert result.tool_calls[0]["error"] == "unknown_tool"
        assert result.status == "completed"

    def test_repeated_identical_calls_are_blocked(self, db: Session) -> None:
        same_call = {"order_id": 4821}
        llm = FakeLLM(
            [
                calls(tool_use("get_order", same_call)),
                calls(tool_use("get_order", same_call)),
                calls(tool_use("get_order", same_call)),
                text("It's processing."),
            ]
        )

        result = run(db, llm, "Status of #4821?")

        assert [c["status"] for c in result.tool_calls] == ["success", "success", "error"]
        assert result.tool_calls[-1]["error"] == "repeated_call"


class TestLoopProtection:
    def test_step_limit_escalates_with_a_valid_history(self, db: Session) -> None:
        config = dataclasses.replace(FAST, max_agent_turns=3)
        llm = FakeLLM([calls(tool_use("get_order", {"order_id": n})) for n in (1, 2, 3)])

        result = run(db, llm, "Check some orders", config=config)

        assert result.status == "escalated"
        assert result.escalation_reason == EscalationReason.STEP_LIMIT
        assert len([c for c in result.llm_calls if c["purpose"] == "agent"]) == 3
        assert_history_is_valid(result.messages)

    def test_tool_call_budget_stops_before_running_extra_calls(self, db: Session) -> None:
        config = dataclasses.replace(FAST, max_tool_calls=1)
        llm = FakeLLM(
            [
                calls(
                    tool_use("cancel_order", {"order_id": 4821}),
                    tool_use("get_order", {"order_id": 8821}),
                )
            ]
        )

        result = run(db, llm, "Cancel #4821 and check #8821", config=config)

        assert result.escalation_reason == EscalationReason.TOOL_CALL_LIMIT
        assert result.tool_calls == []
        assert status_of(db, 4821) == OrderStatus.PROCESSING
        assert_history_is_valid(result.messages)


class TestFailures:
    def test_claude_outage_escalates_with_handoff_message(self, db: Session) -> None:
        llm = FakeLLM([LLMError("Claude API unreachable", transient=True)])

        result = run(db, llm, "Cancel #4821")

        assert result.status == "escalated"
        assert result.escalation_reason == EscalationReason.LLM_UNAVAILABLE
        assert "support team" in result.final_response
        assert result.llm_calls[-1]["error"] == "Claude API unreachable"

    def test_intent_failure_does_not_block_the_run(self, db: Session) -> None:
        llm = FakeLLM(
            [text("Hi! How can I help?")],
            intent_error=LLMError("Claude API error 529", transient=True),
        )

        result = run(db, llm, "Hello")

        assert result.status == "completed"
        assert result.intent is None

    def test_model_refusal_escalates_without_adding_the_reply(self, db: Session) -> None:
        llm = FakeLLM([reply(stop_reason="refusal")])

        result = run(db, llm, "something the model declines")

        assert result.escalation_reason == EscalationReason.MODEL_REFUSAL
        # The refused reply is dropped; only the handoff is recorded.
        assert [m["role"] for m in result.messages] == ["user", "assistant"]
        assert result.messages[-1]["content"][0]["text"] == result.final_response

    def test_transient_database_errors_are_retried(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = {"n": 0}
        real = tool_registry.TOOLS["get_order"]

        def flaky(db: Session, ctx: Any, args: Any) -> Any:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise OperationalError("select", {}, Exception("connection reset"))
            return real.handler(db, ctx, args)

        monkeypatch.setitem(
            tool_registry.TOOLS, "get_order", dataclasses.replace(real, handler=flaky)
        )
        llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])

        result = run(db, llm, "Status of #4821?")

        [call] = result.tool_calls
        assert (call["status"], call["attempts"]) == ("success", 3)

    def test_persistent_database_failure_is_reported_not_hidden(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down(*_: Any) -> Any:
            raise OperationalError("update", {}, Exception("server closed the connection"))

        spec = dataclasses.replace(tool_registry.TOOLS["cancel_order"], handler=down)
        monkeypatch.setitem(tool_registry.TOOLS, "cancel_order", spec)
        llm = FakeLLM(
            [calls(tool_use("cancel_order", {"order_id": 4821})), text("The system is down.")]
        )

        result = run(db, llm, "Cancel #4821")

        [call] = result.tool_calls
        assert (call["error"], call["attempts"]) == ("tool_unavailable", 3)
        [tool_result] = tool_results_in(llm.requests[1])
        assert tool_result["is_error"] is True
        assert status_of(db, 4821) == OrderStatus.PROCESSING

    def test_unexpected_tool_bug_is_contained(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any) -> Any:
            raise KeyError("oops")

        spec = dataclasses.replace(tool_registry.TOOLS["get_order"], handler=broken)
        monkeypatch.setitem(tool_registry.TOOLS, "get_order", spec)
        llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Sorry.")])

        result = run(db, llm, "Status of #4821?")

        assert result.tool_calls[0]["error"] == "internal_error"
        assert result.status == "completed"


def test_before_tool_call_hook_runs_before_each_attempt(db: Session) -> None:
    seen: list[str] = []

    def hook(tool_name: str) -> None:
        seen.append(tool_name)
        if len(seen) == 1:
            raise TimeoutError("simulated")

    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])

    result = run_agent(
        db=db, llm=llm, customer_id=ALICE, message="Status?", config=FAST, before_tool_call=hook
    )

    assert seen == ["get_order", "get_order"]
    assert result.tool_calls[0]["attempts"] == 2


def test_follow_up_turn_after_escalation_has_valid_history(db: Session) -> None:
    first = run(db, FakeLLM([LLMError("down", transient=True)]), "Cancel #4821")
    llm = FakeLLM([text("Sorry about that, how can I help now?")])

    second = run_agent(
        db=db,
        llm=llm,
        customer_id=ALICE,
        message="Are you back?",
        history=first.messages,
        config=FAST,
    )

    sent = llm.requests[0]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0]["text"] == first.final_response
    assert second.status == "completed"


def test_high_value_refund_escalates_for_approval(db: Session) -> None:
    llm = FakeLLM(
        [calls(tool_use("create_return", {"order_id": 7420, "reason": "Screen flickers"}))]
    )

    result = run(db, llm, "Return my laptop #7420, the screen flickers")

    assert result.escalation_reason == EscalationReason.APPROVAL_REQUIRED
    assert result.tool_calls[0]["error"] == "approval_required"
    assert db.query(Return).filter_by(order_id=7420).count() == 0
    assert_history_is_valid(result.messages)
