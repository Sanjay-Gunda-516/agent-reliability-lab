"""The customer-support agent as a LangGraph state machine.

START -> understand_intent -> agent <-> run_tools
                                |           |
                                |           +-> request_approval -> await_decision (pause)
                                |                    resume -> apply_decision -> agent
                                +-> finalize -> END
                                +-> escalate -> END  (also reachable from run_tools)
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from app.agent import approvals
from app.agent.checkpoints import get_checkpointer
from app.agent.config import AgentConfig
from app.agent.llm import LLMClient, LLMError, LLMUsage
from app.agent.prompts import SYSTEM_PROMPTS
from app.agent.state import (
    AgentState,
    EscalationReason,
    LLMCallRecord,
    ToolCallRecord,
)
from app.agent.tool_execution import execute_tool_call
from app.agent.tool_registry import anthropic_tool_definitions
from app.db.lab_models import HumanApprovalRecord
from app.observability.tracing import EventStatus, EventType, TraceRecorder
from app.tools.schemas import ToolContext, ToolErrorCode

TOOL_DEFINITIONS = anthropic_tool_definitions()

HANDOFF_MESSAGE = (
    "I wasn't able to finish this request on my own, so I've passed it to our support team. "
    "Someone will follow up with you shortly."
)
APPROVED_REVIEW_NOTE = (
    "A member of the support team reviewed and approved this request, so it has now been "
    "carried out. It is no longer waiting for review."
)
PENDING_APPROVAL_MESSAGE = (
    "This refund needs to be reviewed by a member of our support team before it can be "
    "processed. I've sent it for review and you'll get an update as soon as it's decided."
)
ESCALATION_MESSAGES: dict[EscalationReason, str] = {
    EscalationReason.LLM_UNAVAILABLE: (
        "I'm having trouble processing requests right now. I've passed yours to our support "
        "team and someone will follow up with you shortly."
    ),
}


@dataclass
class AgentContext:
    """Per-run dependencies. Not part of the state, so never persisted or shown to Claude."""

    db: Session
    llm: LLMClient
    customer_id: int  # from the authenticated session, never from model output
    config: AgentConfig = field(default_factory=AgentConfig)
    sleep: Callable[[float], None] = time.sleep
    before_tool_call: Callable[[str], None] | None = None
    recorder: TraceRecorder = field(default_factory=TraceRecorder)


def _call_record(
    purpose: Literal["intent", "agent"],
    model: str,
    stop_reason: str | None,
    usage: LLMUsage,
    latency_ms: float,
    error: str | None = None,
) -> LLMCallRecord:
    return LLMCallRecord(
        purpose=purpose,
        model=model,
        stop_reason=stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        latency_ms=latency_ms,
        error=error,
    )


def _tool_uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [block for block in content if block.get("type") == "tool_use"]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(b["text"] for b in content if b.get("type") == "text").strip()


def _transcript(messages: list[dict[str, Any]], max_messages: int = 6) -> str:
    lines = []
    for message in messages[-max_messages:]:
        text = _text_of(message["content"])
        if text:
            speaker = "Customer" if message["role"] == "user" else "Assistant"
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def understand_intent(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    with ctx.recorder.span(
        EventType.INTENT, node="understand_intent", model=ctx.config.model
    ) as event:
        try:
            classification = ctx.llm.classify_intent(_transcript(state["messages"]), ctx.config)
        except LLMError as exc:
            # Intent is for evaluation and tracing; an outage is handled by the agent node.
            event.fail(exc)
            return {"intent": None, "llm_calls": [_failed_call("intent", ctx, exc)]}
        event.set_usage(classification.model, classification.usage)
        event.attributes["stop_reason"] = classification.stop_reason
        if classification.result:
            event.attributes["intent"] = str(classification.result.intent)
    return {
        "intent": classification.result.model_dump(mode="json") if classification.result else None,
        "llm_calls": [
            _call_record(
                "intent",
                classification.model,
                classification.stop_reason,
                classification.usage,
                classification.latency_ms,
            )
        ],
    }


def _failed_call(
    purpose: Literal["intent", "agent"], ctx: AgentContext, exc: LLMError
) -> LLMCallRecord:
    return _call_record(purpose, ctx.config.model, None, LLMUsage(), 0.0, error=str(exc))


def agent(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    this_turn = state.get("llm_calls", [])[state.get("turn_start_llm", 0) :]
    turns = sum(1 for call in this_turn if call["purpose"] == "agent")
    if turns >= ctx.config.max_agent_turns:
        return {"escalation_reason": EscalationReason.STEP_LIMIT}

    with ctx.recorder.span(EventType.MODEL_CALL, node="agent", model=ctx.config.model) as event:
        try:
            response = ctx.llm.respond(
                system=SYSTEM_PROMPTS[ctx.config.prompt_version],
                messages=state["messages"],
                tools=TOOL_DEFINITIONS,
                config=ctx.config,
            )
        except LLMError as exc:
            event.fail(exc)
            event.attributes["transient"] = exc.transient
            return {
                "escalation_reason": EscalationReason.LLM_UNAVAILABLE,
                "llm_calls": [_failed_call("agent", ctx, exc)],
            }
        event.set_usage(response.model, response.usage)
        event.attributes["stop_reason"] = response.stop_reason
        if response.stop_reason in ("refusal", "max_tokens"):
            event.fail(response.stop_reason)

    record = _call_record(
        "agent", response.model, response.stop_reason, response.usage, response.latency_ms
    )
    # Neither a refusal nor a cut-off reply is safe to act on or add to the history.
    if response.stop_reason == "refusal":
        return {"escalation_reason": EscalationReason.MODEL_REFUSAL, "llm_calls": [record]}
    if response.stop_reason == "max_tokens":
        return {"escalation_reason": EscalationReason.RESPONSE_TRUNCATED, "llm_calls": [record]}

    update: dict[str, Any] = {
        "messages": [{"role": "assistant", "content": response.content}],
        "llm_calls": [record],
    }
    pending = _tool_uses(response.content)
    tools_this_turn = len(state.get("tool_calls", [])) - state.get("turn_start_tool", 0)
    for block in pending:
        ctx.recorder.point(
            EventType.TOOL_SELECTED,
            node="agent",
            tool_name=block["name"],
            attributes={"tool_use_id": block["id"]},
        )
    if pending and tools_this_turn + len(pending) > ctx.config.max_tool_calls:
        update["escalation_reason"] = EscalationReason.TOOL_CALL_LIMIT
    return update


def run_tools(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    tool_ctx = ToolContext(customer_id=ctx.customer_id)
    # Repeats are judged within the turn; asking again in a later turn is legitimate.
    previous: list[ToolCallRecord] = list(
        state.get("tool_calls", [])[state.get("turn_start_tool", 0) :]
    )
    records: list[ToolCallRecord] = []
    result_blocks: list[dict[str, Any]] = []

    for call in _tool_uses(state["messages"][-1]["content"]):
        execution = execute_tool_call(
            call,
            db=ctx.db,
            tool_ctx=tool_ctx,
            config=ctx.config,
            previous_calls=previous,
            sleep=ctx.sleep,
            before_call=ctx.before_tool_call,
            recorder=ctx.recorder,
        )
        records.append(execution.record)
        previous.append(execution.record)
        result_blocks.append(execution.result_block)

    needs_approval = next(
        (r for r in records if r["error"] == ToolErrorCode.APPROVAL_REQUIRED), None
    )
    if needs_approval is not None:
        # Hold every result of this turn until a person decides; the conversation
        # continues with the real outcome rather than "approval required".
        return {
            "tool_calls": records,
            "pending_tool_results": result_blocks,
            "approval_tool_use_id": needs_approval["tool_use_id"],
        }
    # All results for one assistant turn go back in a single user message.
    return {"messages": [{"role": "user", "content": result_blocks}], "tool_calls": records}


def request_approval(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    call = next(
        r
        for r in reversed(state["tool_calls"])
        if r["tool_use_id"] == state["approval_tool_use_id"]
    )
    approval = approvals.get_or_create(
        ctx.db, execution_id=ctx.recorder.execution_id, customer_id=ctx.customer_id, call=call
    )
    ctx.recorder.point(
        EventType.APPROVAL_REQUESTED,
        node="request_approval",
        tool_name=call["name"],
        attributes={
            "approval_id": approval.id,
            "risk_level": approval.risk_level,
            "refund_amount": approval.refund_amount,
        },
    )
    return {
        "approval_id": approval.id,
        "status": "pending_approval",
        "final_response": PENDING_APPROVAL_MESSAGE,
    }


def await_decision(state: AgentState) -> dict[str, Any]:
    # Pauses the graph; the checkpoint keeps everything until a later request resumes it.
    # This node re-runs from the top on resume, so it must stay free of side effects.
    interrupt({"approval_id": state["approval_id"]})
    return {}


def apply_decision(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Run the approved action (at most once) or substitute the rejection, then hand the
    outcome back to Claude as the tool result it was waiting for."""
    ctx = runtime.context
    approval_id = state["approval_id"]
    assert approval_id is not None
    approval = ctx.db.get(HumanApprovalRecord, approval_id)
    assert approval is not None
    ctx.db.refresh(approval)  # the decision was written by another request
    tool_use_id = approval.tool_use_id
    call = next(
        b for m in reversed(state["messages"]) for b in _tool_uses(m["content"])
        if b["id"] == tool_use_id
    )  # fmt: skip
    ctx.recorder.point(
        EventType.APPROVAL_DECISION,
        node="apply_decision",
        tool_name=approval.tool_name,
        status=EventStatus.OK if approval.status == approvals.APPROVED else EventStatus.REJECTED,
        attributes={
            "approval_id": approval.id,
            "decision": approval.status,
            "decided_by": approval.decided_by,
        },
    )

    new_records: list[ToolCallRecord] = []
    if approval.status == approvals.APPROVED and approvals.claim_action(ctx.db, approval.id):
        execution = execute_tool_call(
            call,
            db=ctx.db,
            tool_ctx=ToolContext(customer_id=ctx.customer_id, approved=True),
            config=ctx.config,
            previous_calls=list(state.get("tool_calls", [])[state.get("turn_start_tool", 0) :]),
            sleep=ctx.sleep,
            before_call=ctx.before_tool_call,
            recorder=ctx.recorder,
        )
        new_records.append(execution.record)
        result_block = execution.result_block
        succeeded = execution.record["status"] == "success"
        approvals.record_outcome(
            ctx.db,
            approval.id,
            action_status=approvals.SUCCEEDED if succeeded else approvals.FAILED,
            result=execution.record["output"] or execution.record["error"],
        )
    elif approval.status == approvals.APPROVED:
        # Already executed by an earlier resume: report that outcome, never run it again.
        result_block = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(approval.action_result, default=str),
        }
    elif approval.status == approvals.REJECTED:
        rejection = {
            "ok": False,
            "error": "rejected_by_reviewer",
            "message": "A member of the support team reviewed this request and declined it.",
            "note": approval.decision_note,
        }
        approvals.record_outcome(ctx.db, approval.id, action_status=None, result=rejection)
        result_block = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": json.dumps(rejection),
        }
    else:
        raise RuntimeError(f"approval {approval.id} resumed while still {approval.status}")

    if approval.status == approvals.APPROVED and not result_block.get("is_error"):
        # Without this, Claude only sees a normal result and may still tell the customer
        # the request is waiting for review.
        outcome = json.loads(result_block["content"])
        if isinstance(outcome, dict) and outcome.get("ok") is not False:
            outcome["review"] = APPROVED_REVIEW_NOTE
            result_block = {**result_block, "content": json.dumps(outcome, default=str)}

    pending = state.get("pending_tool_results") or []
    results = [result_block if b["tool_use_id"] == tool_use_id else b for b in pending]
    return {
        "messages": [{"role": "user", "content": results}],
        "tool_calls": new_records,
        "pending_tool_results": None,
        "approval_tool_use_id": None,
        "approval_id": None,
        "status": None,
        "final_response": None,
    }


def finalize(state: AgentState) -> dict[str, Any]:
    reply = _text_of(state["messages"][-1]["content"])
    if not reply:
        return {
            "status": "escalated",
            "escalation_reason": EscalationReason.EMPTY_RESPONSE,
            "final_response": HANDOFF_MESSAGE,
        }
    return {"status": "completed", "final_response": reply}


def escalate(state: AgentState) -> dict[str, Any]:
    reason = state.get("escalation_reason")
    handoff = ESCALATION_MESSAGES.get(reason, HANDOFF_MESSAGE) if reason else HANDOFF_MESSAGE
    new_messages: list[dict[str, Any]] = []

    # Close out tool calls we didn't run so the history stays valid for a later turn.
    last = state["messages"][-1]
    unresolved = _tool_uses(last["content"]) if last["role"] == "assistant" else []
    if unresolved:
        new_messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": "Not run: the conversation was handed to a human agent.",
                        "is_error": True,
                    }
                    for call in unresolved
                ],
            }
        )
    # Record what the customer was told, so a follow-up turn has the full picture.
    new_messages.append({"role": "assistant", "content": [{"type": "text", "text": handoff}]})
    return {"status": "escalated", "final_response": handoff, "messages": new_messages}


def route_after_agent(state: AgentState) -> Literal["run_tools", "finalize", "escalate"]:
    if state.get("escalation_reason"):
        return "escalate"
    if _tool_uses(state["messages"][-1]["content"]):
        return "run_tools"
    return "finalize"


def route_after_tools(state: AgentState) -> Literal["agent", "escalate", "request_approval"]:
    if state.get("approval_tool_use_id"):
        return "request_approval"
    return "escalate" if state.get("escalation_reason") else "agent"


def build_agent_graph(
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("understand_intent", understand_intent)
    graph.add_node("agent", agent)
    graph.add_node("run_tools", run_tools)
    graph.add_node("finalize", finalize)
    graph.add_node("escalate", escalate)
    graph.add_node("request_approval", request_approval)
    graph.add_node("await_decision", await_decision)
    graph.add_node("apply_decision", apply_decision)

    graph.add_edge(START, "understand_intent")
    graph.add_edge("understand_intent", "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["run_tools", "finalize", "escalate"])
    graph.add_conditional_edges(
        "run_tools", route_after_tools, ["agent", "escalate", "request_approval"]
    )
    graph.add_edge("request_approval", "await_decision")
    graph.add_edge("await_decision", "apply_decision")
    graph.add_edge("apply_decision", "agent")
    graph.add_edge("finalize", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=4)
def _compiled(
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    return build_agent_graph(checkpointer)


def get_agent_graph() -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    # Keyed on the checkpointer, so a reopened checkpointer can't leave a graph holding
    # a closed connection pool.
    return _compiled(get_checkpointer())
