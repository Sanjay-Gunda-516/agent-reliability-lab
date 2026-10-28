"""Trace events emitted by real agent runs (scripted Claude, real tools and database)."""

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.llm import LLMError
from app.agent.service import AgentRunResult, run_agent
from app.evaluation.evaluate import evaluate_variant
from app.evaluation.judge import JudgeVerdict
from app.observability.tracing import EventStatus, EventType, TraceEvent, TraceRecorder
from app.scenarios.loader import load_scenarios
from app.scenarios.runner import run_scenario
from app.scenarios.schema import Persona
from app.seed import ALICE, reset_demo_data
from tests.evaluation_helpers import FakeJudge
from tests.fakes import FakeLLM, calls, text, tool_use

pytestmark = pytest.mark.integration

FAST = AgentConfig(tool_retry_backoff_s=0)
SCENARIOS = {s.id: s for s in load_scenarios()}


@pytest.fixture
def db(db_session: Session) -> Session:
    reset_demo_data(db_session, datetime.now(UTC))
    db_session.commit()
    return db_session


def traced(db: Session, llm: FakeLLM, message: str) -> tuple[AgentRunResult, list[TraceEvent]]:
    recorder = TraceRecorder()
    result = run_agent(
        db=db, llm=llm, customer_id=ALICE, message=message, config=FAST, recorder=recorder
    )
    return result, recorder.events


def kinds(events: list[TraceEvent]) -> list[str]:
    return [f"{e.type}:{e.tool_name}" if e.tool_name else str(e.type) for e in events]


def test_happy_path_timeline(db: Session) -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 4821})), text("Cancelled.")])

    result, events = traced(db, llm, "Cancel #4821")

    assert kinds(events) == [
        "request_received",
        "intent",
        "model_call",
        "tool_selected:cancel_order",
        "tool_call:cancel_order",
        "model_call",
        "response_finalized",
    ]
    assert [e.sequence for e in events] == list(range(1, 8))
    assert all(e.turn == 1 for e in events)
    model_call = events[2]
    assert (model_call.model, model_call.input_tokens, model_call.node) == (
        "fake-claude",
        100,
        "agent",
    )
    assert events[4].status == EventStatus.OK and events[4].attempt == 1
    assert events[0].attributes == {"message_chars": len("Cancel #4821"), "history_messages": 0}
    assert result.execution_id and all(e.duration_ms is None for e in (events[0], events[3]))


def test_rule_rejection_is_its_own_event(db: Session) -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 8821})), text("It shipped.")])

    _, events = traced(db, llm, "Cancel #8821")

    tool_call = next(e for e in events if e.type == EventType.TOOL_CALL)
    rejection = next(e for e in events if e.type == EventType.RULE_REJECTION)
    assert tool_call.status == EventStatus.REJECTED
    assert tool_call.error_type == "cancellation_not_allowed"
    assert rejection.attributes == {
        "error": "cancellation_not_allowed",
        "reason": "already_shipped",
    }
    assert rejection.sequence > tool_call.sequence


def test_retries_appear_inside_the_tool_call() -> None:
    # run_scenario uses its own connection; holding the db fixture's locks would block it.
    run = run_scenario(
        SCENARIOS["tool-timeout-then-recovery"],
        Persona.NORMAL,
        llm=FakeLLM([calls(tool_use("cancel_order", {"order_id": 4821})), text("Done.")]),
        config=FAST,
    )

    tool_call = next(e for e in run.events if e.type == EventType.TOOL_CALL)
    retries = [e for e in run.events if e.type == EventType.TOOL_RETRY]
    assert (tool_call.status, tool_call.attempt) == (EventStatus.OK, 3)
    assert [(r.attempt, r.status, r.error_type) for r in retries] == [
        (1, EventStatus.RETRY, "TimeoutError"),
        (2, EventStatus.RETRY, "TimeoutError"),
    ]
    assert all(tool_call.sequence < r.sequence for r in retries)


def test_outage_marks_the_model_call_failed_and_records_escalation(db: Session) -> None:
    llm = FakeLLM([LLMError("Claude API unreachable", transient=True)])

    _, events = traced(db, llm, "Cancel #4821")

    model_call = next(e for e in events if e.type == EventType.MODEL_CALL)
    assert (model_call.status, model_call.error_type) == (EventStatus.ERROR, "LLMError")
    assert model_call.attributes["transient"] is True
    escalation = next(e for e in events if e.type == EventType.ESCALATION)
    assert escalation.attributes == {"reason": "llm_unavailable"}
    assert events[-1].attributes["status"] == "escalated"


def test_multi_turn_conversation_shares_one_execution() -> None:
    # run_scenario uses its own connection; holding the db fixture's locks would block it.
    run = run_scenario(
        SCENARIOS["clarify-then-cancel"],
        Persona.NORMAL,
        llm=FakeLLM(
            [text("Which one?"), calls(tool_use("cancel_order", {"order_id": 9001})), text("Done.")]
        ),
        config=FAST,
    )

    assert {e.turn for e in run.events} == {1, 2}
    assert [e.type for e in run.events].count(EventType.REQUEST_RECEIVED) == 2
    assert [e.sequence for e in run.events] == list(range(1, len(run.events) + 1))


def test_evaluation_and_judge_are_appended_to_the_same_trace() -> None:
    # run_scenario uses its own connection; holding the db fixture's locks would block it.
    run = run_scenario(
        SCENARIOS["cancel-eligible-order"],
        Persona.NORMAL,
        llm=FakeLLM(
            [calls(tool_use("cancel_order", {"order_id": 4821})), text("Order #4821 cancelled.")]
        ),
        config=FAST,
    )
    conversation_events = len(run.events)
    judge = FakeJudge(JudgeVerdict(behavior="pass", behavior_reason="ok", unsupported_claims=[]))

    evaluate_variant(SCENARIOS["cancel-eligible-order"], run, judge)

    evaluation, judged = run.events[conversation_events:]
    assert (evaluation.type, judged.type) == (EventType.EVALUATION, EventType.JUDGE)
    assert evaluation.attributes["verdict"] == "pass"
    assert judged.attributes == {"behavior": "pass", "unsupported_claims": 0}
    assert judged.model == "fake-judge"


def test_logs_carry_the_execution_id(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    recorder = TraceRecorder()
    with caplog.at_level(logging.INFO, logger="app.trace"):
        run_agent(
            db=db, llm=FakeLLM([text("Hi!")]), customer_id=ALICE, message="hello",
            config=FAST, recorder=recorder,
        )  # fmt: skip

    trace_logs = [r for r in caplog.records if r.name == "app.trace"]
    assert trace_logs
    assert {r.correlation["execution_id"] for r in trace_logs} == {recorder.execution_id}  # type: ignore[attr-defined]
    assert all("hello" not in str(vars(r)) for r in trace_logs)  # message text is never logged
