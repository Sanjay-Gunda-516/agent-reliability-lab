"""A scripted stand-in for Claude so agent tests are fast, free and deterministic."""

import copy
import itertools
from typing import Any

from app.agent.config import AgentConfig
from app.agent.llm import IntentClassification, LLMResponse, LLMUsage
from app.agent.state import Intent, IntentResult

_ids = itertools.count(1)
USAGE = LLMUsage(input_tokens=100, output_tokens=20)


def tool_use(name: str, tool_input: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_{next(_ids)}",
        "name": name,
        "input": tool_input or {},
    }


def reply(*blocks: dict[str, Any], stop_reason: str | None = None) -> LLMResponse:
    has_tool_use = any(b["type"] == "tool_use" for b in blocks)
    return LLMResponse(
        content=list(blocks),
        stop_reason=stop_reason or ("tool_use" if has_tool_use else "end_turn"),
        model="fake-claude",
        usage=USAGE,
        latency_ms=1.0,
    )


def text(message: str) -> LLMResponse:
    return reply({"type": "text", "text": message})


def calls(*tool_blocks: dict[str, Any]) -> LLMResponse:
    return reply(*tool_blocks)


class FakeLLM:
    def __init__(
        self,
        responses: list[LLMResponse | Exception],
        intent: IntentResult | None = None,
        intent_error: Exception | None = None,
    ) -> None:
        self.responses = list(responses)
        self.intent = intent or IntentResult(intent=Intent.OTHER)
        self.intent_error = intent_error
        self.requests: list[list[dict[str, Any]]] = []

    def classify_intent(self, transcript: str, config: AgentConfig) -> IntentClassification:
        if self.intent_error:
            raise self.intent_error
        return IntentClassification(
            result=self.intent,
            model="fake-claude",
            stop_reason="end_turn",
            usage=USAGE,
            latency_ms=1.0,
        )

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse:
        self.requests.append(copy.deepcopy(messages))
        if not self.responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ScriptedByMessage(FakeLLM):
    """Like FakeLLM, but each conversation gets a fresh copy of the script chosen by a
    phrase in the customer's first message, so one fake can serve a whole test run."""

    def __init__(self, scripts: dict[str, list[LLMResponse | Exception]]) -> None:
        super().__init__([])
        self.scripts = scripts

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse:
        if len(messages) == 1:  # a new conversation starts
            first = messages[0]["content"]
            phrase = next((p for p in self.scripts if p in first), None)
            if phrase is None:
                raise AssertionError(f"no script for conversation starting {first!r}")
            self.responses = list(self.scripts[phrase])
        return super().respond(system=system, messages=messages, tools=tools, config=config)
