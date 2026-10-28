import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.graph import AgentContext, get_agent_graph
from app.agent.llm import LLMClient
from app.agent.state import EscalationReason, LLMCallRecord, ToolCallRecord
from app.observability.context import correlate
from app.observability.tracing import EventType, TraceRecorder


@dataclass(frozen=True)
class AgentRunResult:
    execution_id: str
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
    recorder: TraceRecorder | None = None,
) -> AgentRunResult:
    """Run one customer turn. Pass the same recorder for every turn of a conversation."""
    config = config or AgentConfig()
    recorder = recorder or TraceRecorder()
    started = time.perf_counter()
    recorder.start_turn()

    with correlate(execution_id=recorder.execution_id):
        # Message length only: the text itself stays out of logs and events.
        recorder.point(
            EventType.REQUEST_RECEIVED,
            attributes={"message_chars": len(message), "history_messages": len(history or [])},
        )
        context = AgentContext(
            db=db,
            llm=llm,
            customer_id=customer_id,
            config=config,
            before_tool_call=before_tool_call,
            recorder=recorder,
        )
        state = get_agent_graph().invoke(
            {"messages": [*(history or []), {"role": "user", "content": message}]},
            context=context,
            # LangGraph's own cap on node executions, a backstop behind max_agent_turns.
            config={"recursion_limit": 2 * config.max_agent_turns + 5},
        )
        if state["status"] == "escalated":
            recorder.point(
                EventType.ESCALATION,
                node="escalate",
                attributes={"reason": state.get("escalation_reason")},
            )
        recorder.point(
            EventType.RESPONSE_FINALIZED,
            node="escalate" if state["status"] == "escalated" else "finalize",
            attributes={"status": state["status"], "reply_chars": len(state["final_response"])},
        )

    return AgentRunResult(
        execution_id=recorder.execution_id,
        status=state["status"],
        final_response=state["final_response"],
        escalation_reason=state.get("escalation_reason"),
        intent=state.get("intent"),
        tool_calls=state.get("tool_calls", []),
        llm_calls=state.get("llm_calls", []),
        messages=state["messages"],
        latency_ms=(time.perf_counter() - started) * 1000,
    )
