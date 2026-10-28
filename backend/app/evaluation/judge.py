"""A narrow LLM judge for what deterministic checks can't see: whether what the agent
*told the customer* matches the expected behaviour and is supported by the evidence.

It never grades tool choice or database state (deterministic checks already do), and
its findings are reported next to the verdict without changing it.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
from langsmith import traceable
from pydantic import BaseModel, Field

from app.agent.config import Effort
from app.agent.llm import LLMUsage
from app.evaluation.results import CheckResult, CheckSource, Severity
from app.scenarios.runner import ScenarioRun
from app.scenarios.schema import Scenario

logger = logging.getLogger(__name__)

# A different model from the agent's, to avoid a model grading its own replies.
JUDGE_MODEL = "claude-sonnet-5"

JUDGE_SYSTEM_PROMPT = """\
You grade one conversation between an online store's support assistant and a customer.

You get the behaviour a correct assistant shows in this situation, then each turn: the \
customer's message, the tool calls the assistant made with their real results (the \
evidence), and the assistant's reply.

Grade only what the assistant told the customer. Whether the right tools were called and \
whether the store's data ended up correct are checked separately, so don't grade those.

1. behavior: "pass" if the assistant's replies substantially do what the expected \
behaviour describes, otherwise "fail". Ignore wording, tone and length unless the expected \
behaviour mentions them. Extra, harmless help is fine.
2. unsupported_claims: each factual statement made to the customer that the evidence \
doesn't support, such as order details, dates, amounts, statuses, policy terms, or saying \
an action happened when no tool result shows it succeeded. Courtesy and offers of help \
aren't claims. Use an empty list when everything is supported.

Keep behavior_reason to one or two sentences."""


class JudgeVerdict(BaseModel):
    behavior: Literal["pass", "fail"]
    behavior_reason: str = Field(description="One or two sentences explaining the verdict.")
    unsupported_claims: list[str] = Field(
        description="Statements to the customer that the evidence doesn't support."
    )


@dataclass(frozen=True)
class JudgeTurn:
    customer: str
    evidence: list[dict[str, Any]]
    assistant: str
    handed_off_reason: str | None = None


@dataclass(frozen=True)
class JudgeRequest:
    expected_behavior: str
    turns: list[JudgeTurn]

    def render(self) -> str:
        lines = ["## Expected behaviour", self.expected_behavior.strip(), "", "## Conversation"]
        for number, turn in enumerate(self.turns, 1):
            lines += ["", f"### Turn {number}", f"Customer: {turn.customer}"]
            for item in turn.evidence:
                lines.append(
                    f"Tool call: {item['tool']} {json.dumps(item['input'], sort_keys=True)}"
                )
                retries = f", after {item['attempts']} attempts" if item["attempts"] > 1 else ""
                result = json.dumps(item["result"], sort_keys=True)
                lines.append(f"Result ({item['status']}{retries}): {result}")
            if turn.handed_off_reason:
                lines.append(
                    f"System: the conversation was handed to the support team "
                    f"(reason: {turn.handed_off_reason})."
                )
            lines.append(f"Assistant: {turn.assistant}")
        return "\n".join(lines)


@dataclass(frozen=True)
class JudgeResult:
    verdict: JudgeVerdict | None
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    error: str | None = None


class Judge(Protocol):
    @property
    def model(self) -> str: ...

    def judge(self, request: JudgeRequest) -> JudgeResult: ...


def build_judge_request(scenario: Scenario, run: ScenarioRun) -> JudgeRequest:
    """Only what grading the replies needs: no system prompt, no tool expectations."""
    return JudgeRequest(
        expected_behavior=scenario.expected.behavior,
        turns=[
            JudgeTurn(
                customer=turn.message,
                evidence=[
                    {
                        "tool": call["name"],
                        "input": call["input"],
                        "status": call["status"],
                        "attempts": call["attempts"],
                        "result": call["output"] if call["output"] is not None else call["error"],
                    }
                    for call in turn.tool_calls
                ],
                assistant=turn.final_response,
                handed_off_reason=turn.escalation_reason if turn.status == "escalated" else None,
            )
            for turn in run.turns
        ],
    )


def judge_checks(result: JudgeResult) -> list[CheckResult]:
    if result.verdict is None:
        return []
    verdict = result.verdict
    claims = verdict.unsupported_claims
    return [
        CheckResult(
            "judge.behavior",
            Severity.MAJOR,
            verdict.behavior == "pass",
            verdict.behavior_reason,
            "pass",
            verdict.behavior,
            source=CheckSource.JUDGE,
        ),
        CheckResult(
            "judge.grounded",
            Severity.CRITICAL,
            not claims,
            "Every claim is supported by the evidence."
            if not claims
            else f"{len(claims)} unsupported claim(s).",
            [],
            claims,
            source=CheckSource.JUDGE,
        ),
    ]


class ClaudeJudge:
    def __init__(
        self, api_key: str, *, model: str = JUDGE_MODEL, effort: Effort = "medium"
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self._model = model
        self._effort = effort

    @property
    def model(self) -> str:
        return self._model

    @traceable(run_type="chain", name="judge")
    def judge(self, request: JudgeRequest) -> JudgeResult:
        started = time.perf_counter()
        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=4000,
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": request.render()}],
                output_format=JudgeVerdict,
                output_config={"effort": self._effort},
            )
        except anthropic.APIError as exc:
            logger.warning("Judge call failed: %s", exc)
            return JudgeResult(None, self._model, error=f"{type(exc).__name__}: {exc}")

        usage = LLMUsage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return JudgeResult(
                None, response.model, usage, latency_ms, f"no verdict ({response.stop_reason})"
            )
        return JudgeResult(response.parsed_output, response.model, usage, latency_ms)
