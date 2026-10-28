import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.graph import AgentContext, get_agent_graph
from app.agent.llm import LLMClient
from app.agent.state import EscalationReason, LLMCallRecord, ToolCallRecord


@dataclass(frozen=True)
class AgentRunResult:
    status: str
    final_response: str
    escalation_reason: EscalationReason | None
    intent: dict[str, Any] | None
    tool_calls: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord]
    messages: list[dict[str, Any]]
    latency_ms: float


def run_agent(
    *,
    db: Session,
    llm: LLMClient,
    customer_id: int,
    message: str,
    history: list[dict[str, Any]] | None = None,
    config: AgentConfig | None = None,
    before_tool_call: Callable[[str], None] | None = None,
) -> AgentRunResult:
    config = config or AgentConfig()
    started = time.perf_counter()
    context = AgentContext(
        db=db,
        llm=llm,
        customer_id=customer_id,
        config=config,
        before_tool_call=before_tool_call,
    )
    state = get_agent_graph().invoke(
        {"messages": [*(history or []), {"role": "user", "content": message}]},
        context=context,
        # LangGraph's own cap on node executions, a backstop behind max_agent_turns.
        config={"recursion_limit": 2 * config.max_agent_turns + 5},
    )
    return AgentRunResult(
        status=state["status"],
        final_response=state["final_response"],
        escalation_reason=state.get("escalation_reason"),
        intent=state.get("intent"),
        tool_calls=state.get("tool_calls", []),
        llm_calls=state.get("llm_calls", []),
        messages=state["messages"],
        latency_ms=(time.perf_counter() - started) * 1000,
    )
