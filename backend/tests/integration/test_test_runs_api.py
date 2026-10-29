from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import agent as agent_api
from app.api import test_runs as runs_api
from app.config import Settings
from app.db.lab_models import TestRunRecord
from app.db.session import get_sessionmaker
from app.evaluation import __main__ as cli
from app.evaluation.judge import JudgeVerdict
from app.main import app
from tests.evaluation_helpers import FakeJudge
from tests.fakes import ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration

SCRIPTS = {
    "4821": [
        calls(tool_use("cancel_order", {"order_id": 4821})),
        text("Order #4821 is cancelled."),
    ],
    "8821": [calls(tool_use("cancel_order", {"order_id": 8821})), text("Tried to cancel it.")],
}


@pytest.fixture
def api(client: TestClient) -> Iterator[TestClient]:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: ScriptedByMessage(SCRIPTS)
    verdict = JudgeVerdict(behavior="pass", behavior_reason="Fine.", unsupported_claims=[])
    app.dependency_overrides[runs_api.get_judge_factory] = lambda: lambda: FakeJudge(verdict)
    yield client


def start(api: TestClient, **body: Any) -> dict[str, Any]:
    payload = {
        "scenario_ids": ["cancel-eligible-order", "cancel-shipped-order"],
        "personas": ["normal"],
    }
    response = api.post("/test-runs", json={**payload, **body})
    assert response.status_code == 202, response.text
    created: dict[str, Any] = response.json()
    return created


def test_run_lifecycle(api: TestClient) -> None:
    created = start(api, judge=True)
    assert created["variant_count"] == 2

    run = api.get(f"/test-runs/{created['id']}").json()
    assert run["status"] == "completed"
    assert run["judge_model"] == "fake-judge"
    assert run["results_recorded"] == 2
    metrics = run["metrics"]
    assert metrics["task_success"] == {"passed": 1, "total": 2, "rate": 0.5}
    assert metrics["forbidden_tool_violations"] == 1
    assert api.get(f"/test-runs/{created['id']}/metrics").json() == metrics


def test_results_list_and_filter(api: TestClient) -> None:
    run_id = start(api)["id"]

    everything = api.get(f"/test-runs/{run_id}/results").json()
    failures = api.get(f"/test-runs/{run_id}/results", params={"verdict": "fail"}).json()

    assert {r["variant_id"] for r in everything} == {
        "cancel-eligible-order:normal",
        "cancel-shipped-order:normal",
    }
    assert [r["variant_id"] for r in failures] == ["cancel-shipped-order:normal"]
    assert failures[0]["critical_failures"] == 1


def test_result_detail_explains_the_failure(api: TestClient) -> None:
    run_id = start(api, judge=True)["id"]

    detail = api.get(f"/test-runs/{run_id}/results/cancel-shipped-order:normal").json()

    assert detail["verdict"] == "fail"
    forbidden = next(c for c in detail["checks"] if c["name"] == "tools.forbidden")
    assert (forbidden["passed"], forbidden["severity"]) == (False, "critical")
    assert forbidden["actual"][0]["input"] == {"order_id": 8821}
    assert detail["judge_verdict"] == "pass"  # reported separately; the verdict stays fail
    assert detail["trace"]["final_state"]["orders"]["8821"] == "shipped"
    assert len(detail["trace"]["execution_id"]) == 32
    assert "tool_calls" not in detail["trace"]["turns"][0]  # stored as tool_calls rows now


def test_failed_checks_endpoint(api: TestClient) -> None:
    run_id = start(api)["id"]

    critical = api.get(f"/test-runs/{run_id}/failed-checks", params={"severity": "critical"}).json()

    assert [(c["variant_id"], c["name"]) for c in critical] == [
        ("cancel-shipped-order:normal", "tools.forbidden")
    ]


def test_list_runs_newest_first(api: TestClient) -> None:
    first, second = start(api)["id"], start(api)["id"]

    ids = [r["id"] for r in api.get("/test-runs", params={"limit": 2}).json()]

    assert ids == [second, first]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"scenario_ids": ["no-such-scenario"]}, 422),
        ({"personas": ["robot"]}, 422),
        ({"scenario_ids": ["cancel-eligible-order"], "personas": ["incomplete"]}, 422),
    ],
)
def test_invalid_selection(api: TestClient, body: dict[str, Any], status: int) -> None:
    assert api.post("/test-runs", json=body).status_code == status


def test_not_found(api: TestClient) -> None:
    run_id = start(api)["id"]

    assert api.get("/test-runs/999999").status_code == 404
    assert api.get("/test-runs/999999/metrics").status_code == 404
    assert api.get(f"/test-runs/{run_id}/results/no-such:variant").status_code == 404


def run_count() -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(TestRunRecord)) or 0


def test_cli_asks_before_spending_and_creates_nothing_when_declined(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(_env_file=None, database_url="unused", anthropic_api_key="test"),
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")
    before = run_count()

    code = cli.main(["run", "--scenario", "cancel-eligible-order", "--judge"])

    out = capsys.readouterr().out
    assert code == 1 and "Cancelled." in out
    assert "cancel-eligible-order:angry" in out and "3 variant(s), agent v1, judge=on" in out
    assert run_count() == before


def test_cli_show_prints_metrics_and_failures(
    api: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = start(api)["id"]

    assert cli.main(["show", str(run_id)]) == 0

    out = capsys.readouterr().out
    assert '"task_success"' in out
    assert "cancel-shipped-order:normal: [critical/deterministic] tools.forbidden" in out
