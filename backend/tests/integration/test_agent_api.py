from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import agent as agent_api
from app.config import Settings
from app.db.session import get_db
from app.main import app
from app.seed import ALICE, reset_demo_data
from tests.fakes import FakeLLM, calls, text, tool_use

pytestmark = pytest.mark.integration


@pytest.fixture
def api(db_session: Session, client: TestClient) -> Iterator[TestClient]:
    reset_demo_data(db_session, datetime.now(UTC))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    yield client


def use_llm(llm: FakeLLM) -> None:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: llm


def test_runs_the_agent_and_reports_tools_and_usage(api: TestClient) -> None:
    use_llm(FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("It's processing.")]))

    response = api.post("/agent/run", json={"customer_id": ALICE, "message": "Status of #4821?"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["final_response"] == "It's processing."
    assert [c["name"] for c in body["tool_calls"]] == ["get_order"]
    assert body["usage"]["llm_calls"] == 3  # intent + two agent turns
    assert body["usage"]["input_tokens"] == 300


def test_unknown_customer_is_404_and_never_reaches_claude(api: TestClient) -> None:
    llm = FakeLLM([])
    use_llm(llm)

    response = api.post("/agent/run", json={"customer_id": 999999, "message": "hi"})

    assert response.status_code == 404
    assert llm.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        {"customer_id": ALICE, "message": ""},
        {"customer_id": 0, "message": "hi"},
        {"message": "hi"},
        {"customer_id": ALICE, "message": "x" * 4001},
    ],
)
def test_invalid_requests_are_422(api: TestClient, payload: dict[str, object]) -> None:
    use_llm(FakeLLM([]))

    assert api.post("/agent/run", json=payload).status_code == 422


def test_missing_api_key_is_503(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_api, "get_settings", lambda: Settings(_env_file=None, database_url="unused")
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = api.post("/agent/run", json={"customer_id": ALICE, "message": "hi"})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]
