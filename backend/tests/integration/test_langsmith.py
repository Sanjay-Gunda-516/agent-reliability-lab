"""LangSmith integration with a recording client: nothing here talks to the network."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import anthropic
import pytest
from langsmith import Client
from langsmith.utils import tracing_is_enabled
from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.service import run_agent
from app.config import Settings
from app.observability import langsmith as ls
from app.observability.context import correlate
from app.seed import ALICE, reset_demo_data
from tests.fakes import FakeLLM, calls, text, tool_use

pytestmark = pytest.mark.integration


class RecordingClient(Client):
    """Captures runs instead of sending them."""

    def __init__(self) -> None:
        super().__init__(
            api_key="test-not-real", api_url="http://127.0.0.1:9", auto_batch_tracing=False
        )
        self.recorded: list[dict[str, Any]] = []

    def create_run(self, name: str, inputs: Any, run_type: str, *args: Any, **kwargs: Any) -> None:
        metadata = (kwargs.get("extra") or {}).get("metadata", {})
        self.recorded.append({"name": name, "run_type": run_type, "metadata": metadata})

    def update_run(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def fresh_config() -> Iterator[None]:
    ls.get_langsmith.cache_clear()
    yield
    ls.get_langsmith.cache_clear()


def use(monkeypatch: pytest.MonkeyPatch, **settings: Any) -> None:
    base = {"database_url": "unused", "langsmith_tracing": False}
    monkeypatch.setattr(
        ls, "get_settings", lambda: Settings(_env_file=None, **{**base, **settings})
    )


@pytest.fixture
def db(db_session: Session) -> Session:
    reset_demo_data(db_session, datetime.now(UTC))
    db_session.commit()
    return db_session


def chat(db: Session) -> str:
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])
    with correlate(request_id="req-ls-1"):
        result = run_agent(
            db=db, llm=llm, customer_id=ALICE, message="Status?", config=AgentConfig()
        )
    return result.execution_id


class TestConfiguration:
    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(monkeypatch)

        config = ls.get_langsmith()

        assert not config.enabled and config.client is None
        assert config.reason == "LANGSMITH_TRACING is off"

    def test_on_without_a_key_stays_off_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        use(monkeypatch, langsmith_tracing=True)

        with caplog.at_level(logging.WARNING):
            config = ls.get_langsmith()

        assert not config.enabled and config.reason == "LANGSMITH_API_KEY is missing"
        assert "LANGSMITH_API_KEY is missing" in caplog.text

    def test_on_with_a_key_and_the_key_is_never_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        use(
            monkeypatch,
            langsmith_tracing=True,
            langsmith_api_key="lsv2_pt_supersecret123",
            langsmith_project="demo",
        )

        with caplog.at_level(logging.DEBUG):
            config = ls.get_langsmith()

        assert config.enabled and config.project == "demo" and config.client is not None
        assert "supersecret" not in caplog.text


class TestTracing:
    def test_runs_carry_our_correlation_ids(
        self, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = RecordingClient()
        monkeypatch.setattr(ls, "get_langsmith", lambda: ls.LangSmith(True, "test", client))

        execution_id = chat(db)

        names = [r["name"] for r in client.recorded]
        assert names[0] == "support-agent"
        assert {"understand_intent", "agent", "run_tools", "finalize"} <= set(names)
        root = client.recorded[0]["metadata"]
        assert root["execution_id"] == execution_id
        assert root["request_id"] == "req-ls-1"
        assert root["model"] == "claude-opus-5"
        assert all(r["metadata"].get("execution_id") == execution_id for r in client.recorded)

    def test_nothing_is_sent_when_off(self, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        client = RecordingClient()
        monkeypatch.setattr(ls, "get_langsmith", lambda: ls.LangSmith(False, "test", client))

        chat(db)

        assert client.recorded == []

    def test_stray_environment_variables_cannot_turn_it_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use(monkeypatch)
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fromtheshell")

        with ls.langsmith_run({"execution_id": "e1"}):
            assert tracing_is_enabled() is False


class TestClaudeInstrumentation:
    def test_claude_client_is_wrapped_only_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = anthropic.Anthropic(api_key="test")
        original = raw.beta.messages.create

        monkeypatch.setattr(ls, "get_langsmith", lambda: ls.LangSmith(False, "test"))
        assert ls.instrument(raw).beta.messages.create == original

        monkeypatch.setattr(
            ls, "get_langsmith", lambda: ls.LangSmith(True, "test", RecordingClient())
        )
        assert ls.instrument(raw).beta.messages.create != original


def test_unreachable_langsmith_does_not_affect_the_agent(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real client pointed at a closed port: sending happens in the background.
    unreachable = Client(api_key="test-not-real", api_url="http://127.0.0.1:9")
    monkeypatch.setattr(ls, "get_langsmith", lambda: ls.LangSmith(True, "test", unreachable))
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])

    result = run_agent(db=db, llm=llm, customer_id=ALICE, message="Status?", config=AgentConfig())

    assert result.status == "completed" and result.final_response == "Processing."
    assert [c["status"] for c in result.tool_calls] == ["success"]
