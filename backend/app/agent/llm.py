"""Claude access for the agent.

`LLMClient` is the seam tests use to script Claude's replies; `ClaudeClient` is the real
implementation. It isn't a multi-provider abstraction.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaToolParam

from app.agent.config import AgentConfig
from app.agent.prompts import INTENT_PROMPT
from app.agent.state import IntentResult
from app.observability.langsmith import instrument

logger = logging.getLogger(__name__)

# If a safety classifier declines a request, Anthropic re-runs it on its recommended
# fallback model instead of returning the refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str | None
    model: str
    usage: LLMUsage
    latency_ms: float


@dataclass(frozen=True)
class IntentClassification:
    result: IntentResult | None
    model: str
    stop_reason: str | None
    usage: LLMUsage
    latency_ms: float


class LLMError(Exception):
    """Claude couldn't be reached or rejected the request (after SDK retries)."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class LLMClient(Protocol):
    def classify_intent(self, transcript: str, config: AgentConfig) -> IntentClassification: ...

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse: ...


def _usage(raw: Any) -> LLMUsage:
    return LLMUsage(
        input_tokens=raw.input_tokens or 0,
        output_tokens=raw.output_tokens or 0,
        cache_creation_input_tokens=raw.cache_creation_input_tokens or 0,
        cache_read_input_tokens=raw.cache_read_input_tokens or 0,
    )


class ClaudeClient:
    def __init__(self, api_key: str, *, max_retries: int = 2, timeout_s: float = 60.0) -> None:
        # The SDK already retries connection errors, 408/409/429 and 5xx with backoff.
        self._client = instrument(
            anthropic.Anthropic(api_key=api_key, max_retries=max_retries, timeout=timeout_s)
        )

    def classify_intent(self, transcript: str, config: AgentConfig) -> IntentClassification:
        started = time.perf_counter()
        with _translate_errors():
            response = self._client.beta.messages.parse(
                model=config.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": INTENT_PROMPT.format(transcript=transcript)}],
                output_format=IntentResult,
                output_config={"effort": config.intent_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        result = response.parsed_output if response.stop_reason != "refusal" else None
        return IntentClassification(
            result=result,
            model=response.model,
            stop_reason=response.stop_reason,
            usage=_usage(response.usage),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse:
        started = time.perf_counter()
        with _translate_errors():
            response = self._client.beta.messages.create(
                model=config.model,
                max_tokens=config.max_tokens,
                system=system,
                # State keeps plain dicts so it serializes cleanly; they are API params.
                tools=cast(list[BetaToolParam], tools),
                messages=cast(list[BetaMessageParam], messages),
                output_config={"effort": config.effort},
                # Caches the stable tools + system prefix and the growing conversation.
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        return LLMResponse(
            # Replay blocks exactly as returned (tool_use ids, thinking signatures).
            content=[block.to_dict() for block in response.content],
            stop_reason=response.stop_reason,
            model=response.model,
            usage=_usage(response.usage),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


@contextmanager
def _translate_errors() -> Iterator[None]:
    """Turn SDK exceptions into LLMError so the graph can escalate instead of crashing."""
    try:
        yield
    except anthropic.APIConnectionError as exc:  # includes timeouts
        logger.warning("Claude API connection failed: %s", exc)
        raise LLMError("Claude API unreachable", transient=True) from exc
    except anthropic.APIStatusError as exc:
        logger.warning(
            "Claude API error %s (request_id=%s): %s", exc.status_code, exc.request_id, exc.message
        )
        transient = exc.status_code == 429 or exc.status_code >= 500
        raise LLMError(f"Claude API error {exc.status_code}", transient=transient) from exc
