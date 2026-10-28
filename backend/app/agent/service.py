import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.agent.checkpoints import thread_config
from app.agent.config import AgentConfig
from app.agent.graph import AgentContext, get_agent_graph
from app.agent.llm import LLMClient
from app.agent.state import AgentState, EscalationReason, LLMCallRecord, ToolCallRecord
from app.observability.context import correlate, current
from app.observability.langsmith import langsmith_run
from app.observability.tracing import EventType, TraceRecorder


class ExecutionPaused(Exception):
    """The conversation is waiting for a human decision and can't take a new message."""


class NotPaused(Exception):
    """There is nothing to resume for this execution."""


@dataclass(frozen=True)
class AgentRunResult:
    execution_id: str
    status: str  # completed | escalated | pending_approval
    final_response: str
    escalation_reason: EscalationReason | None
    intent: dict[str, Any] | None
    tool_calls: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord]
    messages: list[dict[str, Any]]
    latency_ms: float
    approval_id: int | None = None


def _run_config(execution_id: str, config: AgentConfig) -> RunnableConfig:
    return {
        **thread_config(execution_id),
        "run_name": "support-agent",
        # LangGraph's own cap on node executions, a backstop behind max_agent_turns.
        "recursion_limit": 2 * config.max_agent_turns + 5,
    }


def _invoke(
    graph_input: AgentState | Command[Any],
    *,
    previous: dict[str, Any],
    context: AgentContext,
    started: float,
) -> AgentRunResult:
    """Run the graph from `graph_input`, record how the turn ended, build the result."""
    recorder, config = context.recorder, context.config
    llm_offset = len(previous.get("llm_calls", []))
    tool_offset = len(previous.get("tool_calls", []))
    with langsmith_run(
        {**current().fields(), "model": config.model, "prompt_version": config.prompt_version},
        tags=["support-agent"],
    ):
        state = get_agent_graph().invoke(
            graph_input, _run_config(recorder.execution_id, config), context=context
        )

    paused = "__interrupt__" in state
    status = "pending_approval" if paused else state["status"]
    if paused:
        recorder.point(
            EventType.EXECUTION_PAUSED,
            node="await_decision",
            attributes={"approval_id": state.get("approval_id")},
        )
    elif status == "escalated":
        recorder.point(
            EventType.ESCALATION,
            node="escalate",
            attributes={"reason": state.get("escalation_reason")},
        )
    recorder.point(
        EventType.RESPONSE_FINALIZED,
        node="await_decision" if paused else ("escalate" if status == "escalated" else "finalize"),
        attributes={"status": status, "reply_chars": len(state["final_response"] or "")},
    )
    return AgentRunResult(
        execution_id=recorder.execution_id,
        status=status,
        final_response=state["final_response"],
        escalation_reason=None if paused else state.get("escalation_reason"),
        intent=state.get("intent"),
        tool_calls=state.get("tool_calls", [])[tool_offset:],
        llm_calls=state.get("llm_calls", [])[llm_offset:],
        messages=state["messages"],
        latency_ms=(time.perf_counter() - started) * 1000,
        approval_id=state.get("approval_id") if paused else None,
    )


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
    snapshot = get_agent_graph().get_state(thread_config(recorder.execution_id))
    if snapshot.interrupts:
        raise ExecutionPaused(recorder.execution_id)
    previous = snapshot.values
    recorder.start_turn()

    with correlate(execution_id=recorder.execution_id):
        # Message length only: the text itself stays out of logs and events.
        recorder.point(
            EventType.REQUEST_RECEIVED,
            attributes={
                "message_chars": len(message),
                "history_messages": len(previous.get("messages", [])),
            },
        )
        turn_input = AgentState(
            messages=[{"role": "user", "content": message}],
            # Per-turn fields start fresh; messages and call lists carry over.
            intent=None,
            status=None,
            escalation_reason=None,
            final_response=None,
            turn_start_llm=len(previous.get("llm_calls", [])),
            turn_start_tool=len(previous.get("tool_calls", [])),
        )
        context = AgentContext(
            db=db,
            llm=llm,
            customer_id=customer_id,
            config=config,
            before_tool_call=before_tool_call,
            recorder=recorder,
        )
        return _invoke(turn_input, previous=previous, context=context, started=started)


def resume_agent(
    *,
    db: Session,
    llm: LLMClient,
    customer_id: int,
    recorder: TraceRecorder,
    config: AgentConfig | None = None,
    before_tool_call: Callable[[str], None] | None = None,
) -> AgentRunResult:
    """Continue an execution paused for approval, once the decision has been recorded.

    Runs in whichever request records the decision; the checkpoint supplies everything
    that happened before the pause, and the recorder continues the same trace.
    """
    config = config or AgentConfig()
    started = time.perf_counter()
    snapshot = get_agent_graph().get_state(thread_config(recorder.execution_id))
    if not snapshot.interrupts:
        raise NotPaused(recorder.execution_id)
    previous = snapshot.values

    with correlate(execution_id=recorder.execution_id):
        recorder.point(
            EventType.EXECUTION_RESUMED,
            node="apply_decision",
            attributes={"approval_id": previous.get("approval_id")},
        )
        context = AgentContext(
            db=db,
            llm=llm,
            customer_id=customer_id,
            config=config,
            before_tool_call=before_tool_call,
            recorder=recorder,
        )
        return _invoke(
            Command(resume={"approval_id": previous.get("approval_id")}),
            previous=previous,
            context=context,
            started=started,
        )
