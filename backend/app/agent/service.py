import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.agent.checkpoints import thread_config
from app.agent.config import AgentConfig
from app.agent.graph import AgentContext, get_agent_graph
from app.agent.llm import LLMClient
from app.agent.state import AgentState, EscalationReason, LLMCallRecord, ToolCallRecord
from app.observability.context import correlate, current
from app.observability.langsmith import langsmith_run
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
    config: AgentConfig | None = None,
    before_tool_call: Callable[[str], None] | None = None,
    recorder: TraceRecorder | None = None,
) -> AgentRunResult:
    """Run one customer turn.

    The conversation lives in the LangGraph checkpoint under the recorder's execution_id,
    so a later turn passes the same execution_id (via the recorder) instead of history.
    """
    config = config or AgentConfig()
    recorder = recorder or TraceRecorder()
    started = time.perf_counter()
    graph = get_agent_graph()
    thread = thread_config(recorder.execution_id)
    previous = graph.get_state(thread).values
    recorder.start_turn()

    with correlate(execution_id=recorder.execution_id):
        # Message length only: the text itself stays out of logs and events.
        history = previous.get("messages", [])
        recorder.point(
            EventType.REQUEST_RECEIVED,
            attributes={"message_chars": len(message), "history_messages": len(history)},
        )
        context = AgentContext(
            db=db,
            llm=llm,
            customer_id=customer_id,
            config=config,
            before_tool_call=before_tool_call,
            recorder=recorder,
        )
        with langsmith_run(
            {**current().fields(), "model": config.model, "prompt_version": config.prompt_version},
            tags=["support-agent"],
        ):
            llm_offset = len(previous.get("llm_calls", []))
            tool_offset = len(previous.get("tool_calls", []))
            turn_input = AgentState(
                messages=[{"role": "user", "content": message}],
                # Per-turn fields start fresh; messages and call lists carry over.
                intent=None,
                status=None,
                escalation_reason=None,
                final_response=None,
                turn_start_llm=llm_offset,
                turn_start_tool=tool_offset,
            )
            run_config: RunnableConfig = {
                **thread,
                "run_name": "support-agent",
                # LangGraph's own cap on node executions, a backstop behind max_agent_turns.
                "recursion_limit": 2 * config.max_agent_turns + 5,
            }
            state = graph.invoke(turn_input, run_config, context=context)
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
        tool_calls=state.get("tool_calls", [])[tool_offset:],
        llm_calls=state.get("llm_calls", [])[llm_offset:],
        messages=state["messages"],
        latency_ms=(time.perf_counter() - started) * 1000,
    )
