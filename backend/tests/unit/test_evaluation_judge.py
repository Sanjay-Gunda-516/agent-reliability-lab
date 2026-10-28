from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.agent.prompts import SYSTEM_PROMPTS
from app.evaluation.judge import (
    ClaudeJudge,
    JudgeRequest,
    JudgeResult,
    JudgeVerdict,
    build_judge_request,
    judge_checks,
)
from app.evaluation.results import CheckSource, Severity
from app.scenarios.schema import Scenario
from tests.evaluation_helpers import make_run, make_scenario, tool_call, turn

SCENARIO = make_scenario(
    must_not_call=[{"tool": "create_return"}],
    response={"must_not_mention": ["secret-phrase"]},
)


def request_for(scenario: Scenario = SCENARIO, **turn_kwargs: Any) -> JudgeRequest:
    order = tool_call(
        "cancel_order",
        {"order_id": 4821},
        output={"ok": True, "order_id": 4821, "status": "cancelled", "refund_amount": "89.99"},
    )
    turn_kwargs.setdefault("tools", [order])
    return build_judge_request(scenario, make_run(scenario, turns=[turn(**turn_kwargs)]))


class TestRequest:
    def test_contains_behaviour_conversation_and_evidence(self) -> None:
        text = request_for(reply="Order #4821 is cancelled; $89.99 will be refunded.").render()

        assert SCENARIO.expected.behavior in text
        assert "Customer: Please cancel order #4821." in text
        assert 'Tool call: cancel_order {"order_id": 4821}' in text
        assert '"refund_amount": "89.99"' in text
        assert "Assistant: Order #4821 is cancelled" in text

    def test_leaves_out_everything_the_judge_should_not_grade(self) -> None:
        text = request_for().render()

        assert "Northwind" not in text and SYSTEM_PROMPTS["v1"][:40] not in text
        for hidden in ("must_call", "must_not_call", "create_return", "secret-phrase", "state"):
            assert hidden not in text

    def test_handoff_is_shown_as_evidence(self) -> None:
        text = request_for(
            status="escalated", escalation_reason="llm_unavailable", tools=[]
        ).render()

        assert "handed to the support team (reason: llm_unavailable)" in text

    def test_pending_approval_is_shown_as_evidence(self) -> None:
        text = request_for(status="pending_approval", tools=[]).render()

        assert "(reason: awaiting approval by the support team)" in text

    def test_failed_tool_shows_its_error(self) -> None:
        down = tool_call(
            "cancel_order", {"order_id": 4821}, status="error", error="tool_unavailable"
        )

        assert 'Result (error): "tool_unavailable"' in request_for(tools=[down]).render()


class TestChecks:
    def test_pass_with_no_unsupported_claims(self) -> None:
        verdict = JudgeVerdict(
            behavior="pass", behavior_reason="Confirmed it.", unsupported_claims=[]
        )

        behavior, grounded = judge_checks(JudgeResult(verdict, "m"))

        assert behavior.passed and grounded.passed
        assert {behavior.source, grounded.source} == {CheckSource.JUDGE}
        assert grounded.severity == Severity.CRITICAL

    def test_unsupported_claims_fail_groundedness(self) -> None:
        verdict = JudgeVerdict(
            behavior="fail",
            behavior_reason="Claimed a refund date.",
            unsupported_claims=["Your refund arrives on Friday."],
        )

        behavior, grounded = judge_checks(JudgeResult(verdict, "m"))

        assert not behavior.passed and behavior.reason == "Claimed a refund date."
        assert not grounded.passed and grounded.actual == ["Your refund arrives on Friday."]

    def test_no_verdict_produces_no_checks(self) -> None:
        assert judge_checks(JudgeResult(None, "m", error="boom")) == []


class StubMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def judge_with(outcome: Any) -> tuple[ClaudeJudge, StubMessages]:
    judge = ClaudeJudge("test-key")
    stub = StubMessages(outcome)
    judge._client = SimpleNamespace(messages=stub)  # type: ignore[assignment]
    return judge, stub


USAGE = SimpleNamespace(
    input_tokens=500, output_tokens=60, cache_creation_input_tokens=None, cache_read_input_tokens=0
)


class TestClaudeJudge:
    def test_sends_the_structured_request(self) -> None:
        verdict = JudgeVerdict(behavior="pass", behavior_reason="ok", unsupported_claims=[])
        judge, stub = judge_with(
            SimpleNamespace(
                parsed_output=verdict, stop_reason="end_turn", model="claude-sonnet-5", usage=USAGE
            )
        )

        result = judge.judge(request_for())

        assert result.verdict == verdict and result.error is None
        assert result.usage.input_tokens == 500
        assert stub.kwargs["model"] == "claude-sonnet-5"
        assert stub.kwargs["output_format"] is JudgeVerdict

    def test_api_failure_becomes_an_error_not_an_exception(self) -> None:
        failure = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api"))
        judge, _ = judge_with(failure)

        result = judge.judge(request_for())

        assert result.verdict is None
        assert result.error and "APIConnectionError" in result.error

    @pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
    def test_no_parsed_verdict_is_an_error(self, stop_reason: str) -> None:
        judge, _ = judge_with(
            SimpleNamespace(parsed_output=None, stop_reason=stop_reason, model="m", usage=USAGE)
        )

        result = judge.judge(request_for())

        assert result.verdict is None and result.error == f"no verdict ({stop_reason})"


def test_retries_are_part_of_the_evidence() -> None:
    retried = tool_call("cancel_order", {"order_id": 4821})
    retried["attempts"] = 3

    text = request_for(tools=[retried]).render()

    assert "Result (success, after 3 attempts):" in text
