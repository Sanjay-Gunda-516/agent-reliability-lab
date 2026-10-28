from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agent.config import AgentConfig
from app.api import agent as agent_api
from app.db.lab_models import EvaluationResultRecord, ExecutionTraceRecord
from app.db.session import get_sessionmaker
from app.evaluation.service import RunSelection, create_test_run, execute_test_run
from app.main import app
from app.seed import ALICE, reset_demo_data
from tests.fakes import FakeLLM, ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration


def trace_by_execution_id(execution_id: str) -> ExecutionTraceRecord:
    with get_sessionmaker()() as db:
        trace = db.scalars(
            select(ExecutionTraceRecord).where(ExecutionTraceRecord.execution_id == execution_id)
        ).one()
        _ = trace.events, trace.tool_calls, trace.result
        db.expunge_all()
        return trace


def test_evaluation_run_stores_events_and_tool_calls_as_rows() -> None:
    factory = get_sessionmaker()
    with factory() as db:
        run_id = create_test_run(
            db,
            RunSelection(variant_ids=["tool-timeout-then-recovery:normal"]),
            config=AgentConfig(tool_retry_backoff_s=0),
            judge_model=None,
        ).id
    llm = ScriptedByMessage(
        {"4821": [calls(tool_use("cancel_order", {"order_id": 4821})), text("Cancelled.")]}
    )

    execute_test_run(run_id, session_factory=factory, llm=llm, judge=None)

    with factory() as db:
        result = db.scalars(
            select(EvaluationResultRecord).where(EvaluationResultRecord.test_run_id == run_id)
        ).one()
        execution_id = result.trace.execution_id
    trace = trace_by_execution_id(execution_id)
    assert (trace.source, trace.test_run_id, trace.status) == ("evaluation", run_id, "completed")
    assert trace.variant_id == "tool-timeout-then-recovery:normal"
    assert trace.result is not None and trace.result.id == result.id
    types = [e.type for e in trace.events]
    assert types[0] == "request_received" and types[-1] == "evaluation"
    assert types.count("tool_retry") == 2
    [tool] = trace.tool_calls
    assert (tool.name, tool.status, tool.attempts, tool.turn) == ("cancel_order", "success", 3, 1)
    assert tool.arguments == {"order_id": 4821} and tool.started_at is not None
    assert tool.output and tool.output["status"] == "cancelled"


@pytest.fixture
def api(client: TestClient) -> TestClient:
    with get_sessionmaker()() as db:
        reset_demo_data(db, datetime.now(UTC))
        db.commit()
    return client


def test_api_chat_is_traced_and_correlated(api: TestClient) -> None:
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])
    app.dependency_overrides[agent_api.get_llm_client] = lambda: llm

    response = api.post(
        "/agent/run",
        json={"customer_id": ALICE, "message": "Status of #4821?"},
        headers={"X-Request-ID": "chat-req-1"},
    )

    body = response.json()
    assert body["request_id"] == "chat-req-1" == response.headers["X-Request-ID"]
    trace = trace_by_execution_id(body["execution_id"])
    assert (trace.source, trace.request_id, trace.customer_id) == ("api", "chat-req-1", ALICE)
    assert trace.result is None and trace.test_run_id is None
    assert [t.name for t in trace.tool_calls] == ["get_order"]
    assert [e.sequence for e in trace.events] == list(range(1, len(trace.events) + 1))
    assert trace.turns[0]["message"] == "Status of #4821?"


def test_trace_storage_failure_does_not_break_the_reply(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(agent_api, "build_trace", broken)
    app.dependency_overrides[agent_api.get_llm_client] = lambda: FakeLLM([text("Hello!")])

    response = api.post("/agent/run", json={"customer_id": ALICE, "message": "hi"})

    assert response.status_code == 200
    assert response.json()["final_response"] == "Hello!"
