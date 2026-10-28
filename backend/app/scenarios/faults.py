"""Fault injection for scenarios. Each fault raises what the real failure would raise,
so the agent's existing retry and escalation paths are what get tested."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import OperationalError

from app.agent.config import AgentConfig
from app.agent.llm import IntentClassification, LLMClient, LLMError, LLMResponse
from app.scenarios.schema import FaultSpec, FaultType


@dataclass(frozen=True)
class InjectedFault:
    target: str  # tool name, or "llm"
    type: FaultType


def _tool_exception(fault_type: FaultType) -> Exception:
    if fault_type == FaultType.TOOL_TIMEOUT:
        return TimeoutError("injected: tool call timed out")
    if fault_type == FaultType.DATABASE_ERROR:
        return OperationalError("injected", {}, Exception("injected: database connection lost"))
    return RuntimeError("injected: unexpected tool failure")


class _Budget:
    """How many more times a fault fires; None means every time."""

    def __init__(self, times: int | None) -> None:
        self.remaining = times

    def take(self) -> bool:
        if self.remaining is None:
            return True
        if self.remaining == 0:
            return False
        self.remaining -= 1
        return True


class ToolFaultInjector:
    """Plugged into the agent's `before_tool_call` hook; runs before every attempt."""

    def __init__(self, faults: list[FaultSpec], log: list[InjectedFault]) -> None:
        self._faults = [(f, _Budget(f.times)) for f in faults if f.tool is not None]
        self._log = log

    def __call__(self, tool_name: str) -> None:
        for fault, budget in self._faults:
            if fault.tool == tool_name and budget.take():
                self._log.append(InjectedFault(tool_name, fault.type))
                raise _tool_exception(fault.type)


class FaultyLLM:
    """Wraps an LLM client and fails calls as if Claude were unreachable."""

    def __init__(self, inner: LLMClient, fault: FaultSpec, log: list[InjectedFault]) -> None:
        self._inner = inner
        self._budget = _Budget(fault.times)
        self._log = log

    def _maybe_fail(self) -> None:
        if self._budget.take():
            self._log.append(InjectedFault("llm", FaultType.LLM_UNAVAILABLE))
            raise LLMError("injected: Claude API unreachable", transient=True)

    def classify_intent(self, transcript: str, config: AgentConfig) -> IntentClassification:
        self._maybe_fail()
        return self._inner.classify_intent(transcript, config)

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse:
        self._maybe_fail()
        return self._inner.respond(system=system, messages=messages, tools=tools, config=config)


def apply_faults(
    faults: list[FaultSpec], llm: LLMClient
) -> tuple[LLMClient, ToolFaultInjector | None, list[InjectedFault]]:
    """Returns the (possibly wrapped) LLM client, the tool hook and the shared fault log."""
    log: list[InjectedFault] = []
    llm_faults = [f for f in faults if f.type == FaultType.LLM_UNAVAILABLE]
    if llm_faults:
        llm = FaultyLLM(llm, llm_faults[0], log)
    has_tool_faults = any(f.tool is not None for f in faults)
    injector = ToolFaultInjector(faults, log) if has_tool_faults else None
    return llm, injector, log
