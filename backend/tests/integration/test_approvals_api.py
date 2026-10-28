"""Approval decisions over HTTP: pause in one request, decide and resume in another."""

import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.agent import approvals
from app.api import agent as agent_api
from app.db.lab_models import HumanApprovalRecord
from app.db.models import Return
from app.db.session import get_engine, get_sessionmaker
from app.main import app
from app.seed import ALICE, BEN, reset_demo_data
from tests.fakes import FakeLLM, calls, tool_use
from tests.fakes import text as reply_text

pytestmark = pytest.mark.integration

RETURN_LAPTOP = {"order_id": 7420, "reason": "Screen flickers"}
REVIEWER = {"reviewer": "sam@support.example.com", "note": "Confirmed defect from photos."}


@pytest.fixture
def api(client: TestClient) -> TestClient:
    with get_sessionmaker()() as db:
        reset_demo_data(db, datetime.now(UTC))
        db.commit()
    return client


def use_llm(llm: FakeLLM) -> FakeLLM:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: llm
    return llm


def start_return(api: TestClient) -> dict[str, Any]:
    use_llm(FakeLLM([calls(tool_use("create_return", RETURN_LAPTOP))]))
    response = api.post(
        "/agent/run", json={"customer_id": ALICE, "message": "Return my laptop #7420"}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["status"] == "pending_approval" and body["approval_id"]
    return body


def decide(api: TestClient, approval_id: int, decision: str, follow_up: str = "Done.") -> Any:
    use_llm(FakeLLM([reply_text(follow_up)]))
    return api.post(f"/approvals/{approval_id}/{decision}", json=REVIEWER)


def returns_for_7420() -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(Return).filter_by(order_id=7420)) or 0


def event_types(api: TestClient, execution_id: str) -> list[str]:
    return [e["type"] for e in api.get(f"/traces/{execution_id}/events").json()]


class TestQueue:
    def test_paused_run_shows_up_as_a_pending_approval(self, api: TestClient) -> None:
        run = start_return(api)

        pending = api.get("/approvals", params={"status": "pending"}).json()
        detail = api.get(f"/approvals/{run['approval_id']}").json()

        assert [a["id"] for a in pending] == [run["approval_id"]]
        assert detail["execution_id"] == run["execution_id"]
        assert detail["order_id"] == 7420 and detail["refund_amount"] == "1249.00"
        assert (detail["status"], detail["action_status"]) == ("pending", "not_run")
        assert api.get("/approvals", params={"status": "approved"}).json() == []

    def test_unknown_approval_and_bad_input(self, api: TestClient) -> None:
        run = start_return(api)

        assert api.get("/approvals/999999").status_code == 404
        assert api.post("/approvals/999999/approve", json=REVIEWER).status_code == 404
        assert api.get("/approvals", params={"status": "maybe"}).status_code == 422
        missing_reviewer = api.post(f"/approvals/{run['approval_id']}/approve", json={})
        assert missing_reviewer.status_code == 422


class TestApprove:
    def test_approval_executes_once_and_finishes_the_same_execution(self, api: TestClient) -> None:
        run = start_return(api)

        response = decide(api, run["approval_id"], "approve", "Your return is approved.")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution_id"] == run["execution_id"]
        assert body["execution_status"] == "completed"
        assert body["final_response"] == "Your return is approved."
        approval = body["approval"]
        assert (approval["status"], approval["action_status"]) == ("approved", "succeeded")
        assert approval["decided_by"] == REVIEWER["reviewer"]
        assert approval["resumed_at"] is not None
        assert returns_for_7420() == 1

    def test_trace_continues_across_the_pause(self, api: TestClient) -> None:
        run = start_return(api)
        execution_id = run["execution_id"]
        decide(api, run["approval_id"], "approve")

        events = api.get(f"/traces/{execution_id}/events").json()
        trace = api.get(f"/traces/{execution_id}").json()
        tool_calls = api.get(f"/traces/{execution_id}/tool-calls").json()

        types = [e["type"] for e in events]
        paused, resumed = types.index("execution_paused"), types.index("execution_resumed")
        assert types.index("approval_requested") < paused < resumed
        assert resumed < types.index("approval_decision") < len(types) - 1
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        assert {e["turn"] for e in events} == {1}
        assert trace["status"] == "completed" and trace["counts"]["turns"] == 1
        # Refused for approval in the first request, executed in the second.
        assert [(c["name"], c["status"]) for c in tool_calls] == [
            ("create_return", "rejected"),
            ("create_return", "success"),
        ]

    def test_second_approval_is_refused_and_nothing_runs_again(self, api: TestClient) -> None:
        run = start_return(api)
        assert decide(api, run["approval_id"], "approve").status_code == 200

        again = decide(api, run["approval_id"], "approve")
        reject_after = decide(api, run["approval_id"], "reject")

        assert again.status_code == 409 and "already approved" in again.json()["detail"]
        assert reject_after.status_code == 409
        assert returns_for_7420() == 1
        assert event_types(api, run["execution_id"]).count("execution_resumed") == 1

    def test_concurrent_approvals_execute_once(self, api: TestClient) -> None:
        run = start_return(api)
        use_llm(FakeLLM([reply_text("Approved.")]))  # one follow-up: only one may resume
        barrier = threading.Barrier(3)
        statuses: list[int] = []

        def approve() -> None:
            barrier.wait()
            response = api.post(f"/approvals/{run['approval_id']}/approve", json=REVIEWER)
            statuses.append(response.status_code)

        threads = [threading.Thread(target=approve) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert sorted(statuses) == [200, 409, 409]
        assert returns_for_7420() == 1

    def test_decision_in_progress_elsewhere_is_refused(self, api: TestClient) -> None:
        run = start_return(api)

        with get_engine().connect() as other:
            other.execute(text("SELECT pg_advisory_lock(:id)"), {"id": run["approval_id"]})
            response = decide(api, run["approval_id"], "approve")
            other.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": run["approval_id"]})
            other.commit()

        assert response.status_code == 409
        assert "being processed" in response.json()["detail"]
        assert api.get(f"/approvals/{run['approval_id']}").json()["status"] == "pending"

    def test_retry_finishes_a_decision_whose_resume_never_ran(self, api: TestClient) -> None:
        # e.g. the process died between recording the decision and resuming.
        run = start_return(api)
        with get_sessionmaker()() as db:
            approval = db.get(HumanApprovalRecord, run["approval_id"])
            assert approval is not None
            approval.status, approval.decided_by = approvals.APPROVED, "sam@support.example.com"
            approval.decided_at = datetime.now(UTC)
            db.commit()

        assert decide(api, run["approval_id"], "reject").status_code == 409
        retry = decide(api, run["approval_id"], "approve")

        assert retry.status_code == 200, retry.text
        assert retry.json()["approval"]["action_status"] == "succeeded"
        assert returns_for_7420() == 1


class TestReject:
    def test_rejected_action_never_executes(self, api: TestClient) -> None:
        run = start_return(api)

        response = decide(api, run["approval_id"], "reject", "Sorry, it was declined.")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["execution_status"] == "completed"
        assert body["approval"]["status"] == "rejected"
        assert body["approval"]["decision_note"] == REVIEWER["note"]
        assert body["approval"]["action_status"] == "not_run"
        assert returns_for_7420() == 0
        assert decide(api, run["approval_id"], "approve").status_code == 409
        assert returns_for_7420() == 0
        decision = next(
            e
            for e in api.get(f"/traces/{run['execution_id']}/events").json()
            if e["type"] == "approval_decision"
        )
        assert decision["status"] == "rejected"


class TestContinuingTheConversation:
    def test_new_message_while_paused_is_refused(self, api: TestClient) -> None:
        run = start_return(api)
        use_llm(FakeLLM([]))

        response = api.post(
            "/agent/run",
            json={
                "customer_id": ALICE,
                "message": "Any news?",
                "execution_id": run["execution_id"],
            },
        )

        assert response.status_code == 409

    def test_someone_elses_conversation_is_not_found(self, api: TestClient) -> None:
        run = start_return(api)
        use_llm(FakeLLM([]))

        response = api.post(
            "/agent/run",
            json={"customer_id": BEN, "message": "Hi", "execution_id": run["execution_id"]},
        )

        assert response.status_code == 404

    def test_follow_up_after_the_decision_adds_a_turn(self, api: TestClient) -> None:
        run = start_return(api)
        decide(api, run["approval_id"], "approve")
        llm = use_llm(FakeLLM([reply_text("You're welcome!")]))

        response = api.post(
            "/agent/run",
            json={"customer_id": ALICE, "message": "Thanks!", "execution_id": run["execution_id"]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["execution_id"] == run["execution_id"]
        # Claude sees the whole conversation, including the approved return.
        assert len(llm.requests[0]) > 1
        trace = api.get(f"/traces/{run['execution_id']}").json()
        assert trace["counts"]["turns"] == 2
        events = api.get(f"/traces/{run['execution_id']}/events").json()
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        assert events[-1]["turn"] == 2
