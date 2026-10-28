from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMError
from app.api import agent as agent_api
from app.api import test_runs as runs_api
from app.db.session import get_sessionmaker
from app.evaluation.judge import JudgeVerdict
from app.main import app
from app.seed import ALICE, reset_demo_data
from tests.evaluation_helpers import FakeJudge
from tests.fakes import FakeLLM, ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration


@pytest.fixture
def api(client: TestClient) -> TestClient:
    with get_sessionmaker()() as db:
        reset_demo_data(db, datetime.now(UTC))
        db.commit()
    return client


def chat(api: TestClient, llm: Any, message: str = "Cancel #8821") -> dict[str, Any]:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: llm
    response = api.post("/agent/run", json={"customer_id": ALICE, "message": message})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_trace_detail_summarizes_timing_tokens_and_counts(api: TestClient) -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 8821})), text("It shipped.")])
    execution_id = chat(api, llm)["execution_id"]

    trace = api.get(f"/traces/{execution_id}").json()

    assert trace["source"] == "api" and trace["status"] == "completed"
    assert trace["correlation"]["execution_id"] == execution_id
    assert trace["correlation"]["request_id"] == trace["request_id"]
    assert trace["agent_tokens"] == {
        "calls": 3,  # intent + two agent turns, 100 in / 20 out each from the fake
        "input_tokens": 300,
        "output_tokens": 60,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert trace["judge_tokens"]["calls"] == 0
    assert trace["counts"] == {
        "events": 8, "turns": 1, "tool_calls": 1, "retries": 0, "errors": 0, "rule_rejections": 1,
    }  # fmt: skip
    timing = trace["timing"]
    assert timing["total_ms"] >= timing["model_ms"] >= 0 and timing["tool_ms"] >= 0


def test_events_timeline_and_filters(api: TestClient) -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 8821})), text("It shipped.")])
    execution_id = chat(api, llm)["execution_id"]

    events = api.get(f"/traces/{execution_id}/events").json()
    model_calls = api.get(f"/traces/{execution_id}/events", params={"type": "model_call"}).json()
    rejected = api.get(f"/traces/{execution_id}/events", params={"status": "rejected"}).json()

    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert [e["type"] for e in events][:3] == ["request_received", "intent", "model_call"]
    assert len(model_calls) == 2 and model_calls[0]["input_tokens"] == 100
    assert {e["type"] for e in rejected} == {"tool_call", "rule_rejection"}


def test_errors_are_visible_in_the_timeline(api: TestClient) -> None:
    execution_id = chat(api, FakeLLM([LLMError("Claude API unreachable", transient=True)]))[
        "execution_id"
    ]

    trace = api.get(f"/traces/{execution_id}").json()
    errors = api.get(f"/traces/{execution_id}/events", params={"status": "error"}).json()

    assert trace["status"] == "escalated" and trace["escalation_reason"] == "llm_unavailable"
    assert trace["counts"]["errors"] == 1
    [model_call] = errors
    assert (model_call["type"], model_call["error_type"]) == ("model_call", "LLMError")


def test_tool_calls_endpoint(api: TestClient) -> None:
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])
    execution_id = chat(api, llm, "Status of #4821?")["execution_id"]

    [tool] = api.get(f"/traces/{execution_id}/tool-calls").json()

    assert (tool["name"], tool["status"], tool["attempts"], tool["turn"]) == (
        "get_order",
        "success",
        1,
        1,
    )
    assert tool["arguments"] == {"order_id": 4821}
    assert tool["output"]["order"]["order_id"] == 4821


def test_evaluation_traces_link_back_to_their_result(api: TestClient) -> None:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: ScriptedByMessage(
        {
            "4821": [
                calls(tool_use("cancel_order", {"order_id": 4821})),
                text("Order #4821 cancelled."),
            ]
        }
    )
    verdict = JudgeVerdict(behavior="pass", behavior_reason="ok", unsupported_claims=[])
    app.dependency_overrides[runs_api.get_judge_factory] = lambda: lambda: FakeJudge(verdict)
    run_id = api.post(
        "/test-runs", json={"variant_ids": ["cancel-eligible-order:normal"], "judge": True}
    ).json()["id"]

    [summary] = api.get("/traces", params={"test_run_id": run_id}).json()
    trace = api.get(f"/traces/{summary['execution_id']}").json()
    result = api.get(f"/test-runs/{run_id}/results/cancel-eligible-order:normal").json()

    assert (
        summary["source"] == "evaluation"
        and summary["variant_id"] == "cancel-eligible-order:normal"
    )
    assert trace["correlation"]["evaluation_result_id"] is not None
    assert result["trace"]["execution_id"] == summary["execution_id"]
    assert trace["judge_tokens"]["calls"] == 1
    assert trace["timing"]["evaluation_ms"] > 0
    assert trace["final_state"]["orders"]["4821"] == "cancelled"


def test_list_filters_and_not_found(api: TestClient) -> None:
    chat(api, FakeLLM([text("Hi")]), "hello")

    api_traces = api.get("/traces", params={"source": "api", "limit": 1}).json()

    assert len(api_traces) == 1 and api_traces[0]["source"] == "api"
    assert api.get("/traces", params={"source": "other"}).status_code == 422
    assert api.get("/traces/does-not-exist").status_code == 404
    assert api.get("/traces/does-not-exist/events").status_code == 404
    assert api.get("/traces/does-not-exist/tool-calls").status_code == 404
