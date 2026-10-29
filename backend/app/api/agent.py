import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.config import AgentConfig
from app.agent.llm import ClaudeClient, LLMClient
from app.agent.service import AgentRunResult, ExecutionPaused, run_agent
from app.agent.state import EscalationReason, IntentResult, ToolCallRecord
from app.agent.versions import DEFAULT_AGENT_VERSION, get_version
from app.config import get_settings
from app.db.lab_models import ExecutionTraceRecord
from app.db.models import Customer
from app.db.session import DbSession
from app.observability.context import current
from app.observability.trace_store import append_to_trace, build_trace, continue_recorder
from app.observability.tracing import TraceRecorder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    # Stands in for an authenticated session; a real deployment would take this from
    # the auth token, not the request body.
    customer_id: int = Field(gt=0, description="The signed-in customer.")
    message: str = Field(min_length=1, max_length=4000)
    execution_id: str | None = Field(
        default=None,
        max_length=32,
        description="Continue this conversation instead of starting a new one.",
    )


class UsageSummary(BaseModel):
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class AgentRunResponse(BaseModel):
    execution_id: str
    request_id: str | None
    status: str
    final_response: str
    escalation_reason: EscalationReason | None
    intent: IntentResult | None
    tool_calls: list[ToolCallRecord]
    usage: UsageSummary
    latency_ms: float
    approval_id: int | None = None


@lru_cache
def _claude_client(api_key: str) -> ClaudeClient:
    return ClaudeClient(api_key)


def get_llm_client() -> LLMClient:
    api_key = get_settings().anthropic_api_key
    if api_key is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent is not configured: ANTHROPIC_API_KEY is not set.",
        )
    return _claude_client(api_key.get_secret_value())


@router.post("/run", response_model=AgentRunResponse)
def run(
    request: AgentRunRequest,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> AgentRunResponse:
    if db.get(Customer, request.customer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found.")

    config = get_version(DEFAULT_AGENT_VERSION).config
    trace = _trace_to_continue(db, request) if request.execution_id else None
    recorder = continue_recorder(trace) if trace else TraceRecorder()
    started_at = datetime.now(UTC)
    try:
        result = run_agent(
            db=db,
            llm=llm,
            customer_id=request.customer_id,
            message=request.message,
            config=config,
            recorder=recorder,
        )
    except ExecutionPaused as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This conversation is waiting for a support team decision.",
        ) from exc
    _save_trace(db, request, result, recorder, config, started_at, trace)

    calls = result.llm_calls
    return AgentRunResponse(
        execution_id=result.execution_id,
        request_id=current().request_id,
        status=result.status,
        final_response=result.final_response,
        escalation_reason=result.escalation_reason,
        intent=IntentResult.model_validate(result.intent) if result.intent else None,
        tool_calls=result.tool_calls,
        usage=UsageSummary(
            llm_calls=len(calls),
            input_tokens=sum(c["input_tokens"] for c in calls),
            output_tokens=sum(c["output_tokens"] for c in calls),
            cache_creation_input_tokens=sum(c["cache_creation_input_tokens"] for c in calls),
            cache_read_input_tokens=sum(c["cache_read_input_tokens"] for c in calls),
        ),
        latency_ms=result.latency_ms,
        approval_id=result.approval_id,
    )


def _trace_to_continue(db: DbSession, request: AgentRunRequest) -> ExecutionTraceRecord:
    trace = db.scalars(
        select(ExecutionTraceRecord).where(
            ExecutionTraceRecord.execution_id == request.execution_id,
            ExecutionTraceRecord.source == "api",
        )
    ).one_or_none()
    # Someone else's conversation looks the same as one that doesn't exist.
    if trace is None or trace.customer_id != request.customer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return trace


def _save_trace(
    db: DbSession,
    request: AgentRunRequest,
    result: AgentRunResult,
    recorder: TraceRecorder,
    config: AgentConfig,
    started_at: datetime,
    existing: ExecutionTraceRecord | None = None,
) -> None:
    # The customer's request has already been acted on; a failure to store its trace is
    # logged rather than turned into an error response.
    turn = {
        "message": request.message,
        "status": result.status,
        "final_response": result.final_response,
        "escalation_reason": result.escalation_reason,
        "intent": result.intent,
        "latency_ms": result.latency_ms,
        "approval_id": result.approval_id,
    }
    try:
        if existing is not None:
            append_to_trace(
                existing,
                events=recorder.events,
                tool_calls=result.tool_calls,
                messages=result.messages,
                status=result.status,
                escalation_reason=result.escalation_reason,
                finished_at=datetime.now(UTC),
                new_turn=turn,
            )
            db.commit()
            return
        db.add(
            build_trace(
                execution_id=result.execution_id,
                source="api",
                customer_id=request.customer_id,
                model=config.model,
                agent_version=DEFAULT_AGENT_VERSION,
                status=result.status,
                escalation_reason=result.escalation_reason,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                turns=[turn],
                messages=result.messages,
                tool_calls=result.tool_calls,
                events=recorder.events,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not store the execution trace")
