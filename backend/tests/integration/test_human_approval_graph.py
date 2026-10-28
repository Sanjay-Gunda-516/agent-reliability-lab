"""Pause and resume around a high-risk action, at the graph/service level."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent import approvals
from app.agent.checkpoints import thread_config
from app.agent.config import AgentConfig
from app.agent.graph import APPROVED_REVIEW_NOTE, get_agent_graph
from app.agent.service import (
    AgentRunResult,
    ExecutionPaused,
    NotPaused,
    resume_agent,
    run_agent,
)
from app.db.lab_models import HumanApprovalRecord
from app.db.models import Return
from app.observability.tracing import EventType, TraceRecorder
from app.seed import ALICE, reset_demo_data
from tests.fakes import FakeLLM, calls, text, tool_use

pytestmark = pytest.mark.integration

FAST = AgentConfig(tool_retry_backoff_s=0)
RETURN_LAPTOP = {"order_id": 7420, "reason": "Screen flickers"}


@pytest.fixture
def db(db_session: Session) -> Session:
    reset_demo_data(db_session, datetime.now(UTC))
    db_session.commit()
    return db_session


def pause(db: Session, recorder: TraceRecorder, *extra_calls: dict[str, Any]) -> AgentRunResult:
    llm = FakeLLM([calls(*extra_calls, tool_use("create_return", RETURN_LAPTOP))])
    return run_agent(
        db=db, llm=llm, customer_id=ALICE, message="Return my laptop #7420", config=FAST,
        recorder=recorder,
    )  # fmt: skip


def decide(db: Session, approval_id: int, status: str, note: str | None = None) -> None:
    approval = db.get(HumanApprovalRecord, approval_id)
    assert approval is not None
    approval.status, approval.decided_by = status, "reviewer@example.com"
    approval.decision_note, approval.decided_at = note, datetime.now(UTC)
    db.commit()


def resume(db: Session, recorder: TraceRecorder, reply: str) -> tuple[AgentRunResult, FakeLLM]:
    llm = FakeLLM([text(reply)])
    result = resume_agent(db=db, llm=llm, customer_id=ALICE, recorder=recorder, config=FAST)
    return result, llm


def returns_for_7420(db: Session) -> int:
    return db.query(Return).filter_by(order_id=7420).count()


def last_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    users = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
    return users[-1]["content"]


class TestPause:
    def test_pauses_with_a_pending_approval_and_nothing_executed(self, db: Session) -> None:
        recorder = TraceRecorder()

        result = pause(db, recorder)

        assert result.status == "pending_approval"
        assert "review" in result.final_response
        approval = db.get(HumanApprovalRecord, result.approval_id)
        assert approval is not None
        assert (approval.status, approval.action_status) == ("pending", "not_run")
        assert (approval.order_id, str(approval.refund_amount), approval.risk_level) == (
            7420,
            "1249.00",
            "high",
        )
        assert approval.customer_reason == "Screen flickers"
        assert "$500.00" in approval.reason and approval.execution_id == recorder.execution_id
        assert returns_for_7420(db) == 0
        types = [e.type for e in recorder.events]
        assert types[-3:] == [
            EventType.APPROVAL_REQUESTED,
            EventType.EXECUTION_PAUSED,
            EventType.RESPONSE_FINALIZED,
        ]

    def test_checkpoint_holds_no_app_types(self, db: Session) -> None:
        # LangGraph will refuse to load unregistered classes from checkpoints.
        recorder = TraceRecorder()
        pause(db, recorder)

        state = get_agent_graph().get_state(thread_config(recorder.execution_id)).values

        [call] = state["tool_calls"]
        assert type(call["error"]) is str and call["error"] == "approval_required"

    def test_a_new_message_is_refused_while_paused(self, db: Session) -> None:
        recorder = TraceRecorder()
        pause(db, recorder)

        with pytest.raises(ExecutionPaused):
            run_agent(
                db=db, llm=FakeLLM([]), customer_id=ALICE, message="hello?", recorder=recorder
            )


class TestDecision:
    def test_approval_executes_the_action_once_and_claude_replies(self, db: Session) -> None:
        recorder = TraceRecorder()
        paused = pause(db, recorder)
        assert paused.approval_id is not None
        decide(db, paused.approval_id, "approved")

        result, llm = resume(db, recorder, "Your return is approved: $1,249.00 is on its way.")

        assert result.status == "completed" and result.execution_id == recorder.execution_id
        assert returns_for_7420(db) == 1
        [call] = result.tool_calls
        assert (call["name"], call["status"]) == ("create_return", "success")
        approval = db.get(HumanApprovalRecord, paused.approval_id)
        assert approval is not None and approval.action_status == "succeeded"
        assert approval.action_result["order_id"] == 7420 and approval.resumed_at is not None
        # Claude sees the real outcome as the result of the call it made before the pause.
        [result_block] = last_tool_results(llm.requests[0])
        outcome = json.loads(result_block["content"])
        assert outcome["status"] == "requested" and outcome["return_id"]
        # ...and is told the review is over, so it doesn't say it's still pending.
        assert outcome["review"] == APPROVED_REVIEW_NOTE
        types = [e.type for e in recorder.events]
        resumed = types.index(EventType.EXECUTION_RESUMED)
        assert types[resumed:] == [
            EventType.EXECUTION_RESUMED,
            EventType.APPROVAL_DECISION,
            EventType.TOOL_CALL,
            EventType.MODEL_CALL,
            EventType.RESPONSE_FINALIZED,
        ]

    def test_rejection_never_executes_the_action(self, db: Session) -> None:
        recorder = TraceRecorder()
        paused = pause(db, recorder)
        assert paused.approval_id is not None
        decide(db, paused.approval_id, "rejected", note="Outside warranty terms")

        result, llm = resume(db, recorder, "Sorry, the refund was declined after review.")

        assert result.status == "completed" and result.tool_calls == []
        assert returns_for_7420(db) == 0
        [result_block] = last_tool_results(llm.requests[0])
        assert "rejected_by_reviewer" in result_block["content"]
        assert "Outside warranty terms" in result_block["content"]
        assert "review" not in json.loads(result_block["content"])
        decision = next(e for e in recorder.events if e.type == EventType.APPROVAL_DECISION)
        assert decision.attributes["decision"] == "rejected"
        approval = db.get(HumanApprovalRecord, paused.approval_id)
        assert approval is not None and approval.action_status == "not_run"

    def test_an_already_executed_action_is_reported_not_rerun(self, db: Session) -> None:
        recorder = TraceRecorder()
        paused = pause(db, recorder)
        assert paused.approval_id is not None
        decide(db, paused.approval_id, "approved")
        # Simulate a previous resume that executed the action but crashed before finishing.
        assert approvals.claim_action(db, paused.approval_id)
        approvals.record_outcome(
            db, paused.approval_id, action_status="succeeded", result={"ok": True, "return_id": 99}
        )

        result, llm = resume(db, recorder, "Your return was already created.")

        assert result.tool_calls == [] and returns_for_7420(db) == 0
        [result_block] = last_tool_results(llm.requests[0])
        outcome = json.loads(result_block["content"])
        assert outcome["return_id"] == 99 and outcome["review"] == APPROVED_REVIEW_NOTE

    def test_resuming_before_a_decision_fails_without_executing(self, db: Session) -> None:
        recorder = TraceRecorder()
        pause(db, recorder)

        with pytest.raises(RuntimeError, match="still pending"):
            resume(db, recorder, "unused")
        assert returns_for_7420(db) == 0

    def test_a_finished_execution_cannot_be_resumed_again(self, db: Session) -> None:
        recorder = TraceRecorder()
        paused = pause(db, recorder)
        assert paused.approval_id is not None
        decide(db, paused.approval_id, "approved")
        resume(db, recorder, "Approved.")

        with pytest.raises(NotPaused):
            resume(db, recorder, "again?")
        assert returns_for_7420(db) == 1


def test_other_results_from_the_same_turn_are_held_and_delivered_together(db: Session) -> None:
    recorder = TraceRecorder()
    paused = pause(db, recorder, tool_use("get_order", {"order_id": 7420}))
    assert paused.approval_id is not None
    decide(db, paused.approval_id, "approved")

    _, llm = resume(db, recorder, "Done.")

    blocks = last_tool_results(llm.requests[0])
    assert [b["type"] for b in blocks] == ["tool_result", "tool_result"]
    assert '"order_id":7420' in blocks[0]["content"].replace(" ", "")  # get_order result
    assert "approval_required" not in blocks[1]["content"]
