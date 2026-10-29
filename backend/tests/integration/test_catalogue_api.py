"""Read-only endpoints the dashboard uses: the scenario catalogue and the run list."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import agent as agent_api
from app.main import app
from app.scenarios.loader import load_scenarios
from tests.fakes import ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration

SCRIPT = {
    "4821": [
        calls(tool_use("get_order", {"order_id": 4821})),
        calls(tool_use("cancel_order", {"order_id": 4821})),
        text("Order #4821 is cancelled."),
    ]
}


@pytest.fixture
def api(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: ScriptedByMessage(SCRIPT)
    yield client


def start(api: TestClient, **body: Any) -> int:
    payload = {"variant_ids": ["cancel-eligible-order:normal"], **body}
    response = api.post("/test-runs", json=payload)
    assert response.status_code == 202, response.text
    run_id: int = response.json()["id"]
    return run_id


class TestScenarios:
    def test_list_matches_the_scenario_files(self, api: TestClient) -> None:
        start(api)  # a run syncs the files into the database

        listed = api.get("/scenarios").json()

        assert {s["id"] for s in listed} == {s.id for s in load_scenarios()}
        cancel = next(s for s in listed if s["id"] == "cancel-eligible-order")
        assert cancel["expected_outcome"] == "order_cancelled"
        assert cancel["expected_status"] == "completed"
        assert "cancel-eligible-order:normal" in {v["id"] for v in cancel["variants"]}
        assert "definition" not in cancel

    def test_filter_by_tag(self, api: TestClient) -> None:
        start(api)

        tagged = api.get("/scenarios", params={"tag": "failure-recovery"}).json()

        assert tagged and all("failure-recovery" in s["tags"] for s in tagged)

    def test_detail_includes_the_expectations(self, api: TestClient) -> None:
        start(api)

        detail = api.get("/scenarios/cancel-shipped-order").json()

        assert detail["definition"]["expected"]["must_not_call"] == [
            {"tool": "cancel_order", "args": None, "result": "any"}
        ]
        assert api.get("/scenarios/nope").status_code == 404


class TestRunList:
    def test_list_includes_the_stored_metrics(self, api: TestClient) -> None:
        run_id = start(api)

        [item] = [r for r in api.get("/test-runs").json() if r["id"] == run_id]

        assert item["metrics"]["task_success"] == {"passed": 1, "total": 1, "rate": 1.0}
        assert item["check_set_version"] == "checks-v2"

    def test_filters(self, api: TestClient) -> None:
        v1, regressed = start(api), start(api, agent_version="v1-regressed")

        by_version = [r["id"] for r in api.get("/test-runs", params={"agent_version": "v1"}).json()]
        completed = api.get("/test-runs", params={"status": "completed"}).json()

        assert v1 in by_version and regressed not in by_version
        assert {r["status"] for r in completed} == {"completed"}
        assert api.get("/test-runs", params={"status": "nope"}).status_code == 422
