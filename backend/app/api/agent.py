import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.config import AgentConfig
from app.agent.llm import ClaudeClient, LLMClient
from app.agent.service import AgentRunResult, run_agent
from app.agent.state import EscalationReason, IntentResult, ToolCallRecord
from app.config import get_settings
from app.db.models import Customer
from app.db.session import DbSession
from app.observability.context import current
from app.observability.trace_store import build_trace
from app.observability.tracing import TraceRecorder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    # Stands in for an authenticated session; a real deployment would take this from
    # the auth token, not the request body.
    customer_id: int = Field(gt=0, description="The signed-in customer.")
    message: str = Field(min_length=1, max_length=4000)


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

    config = AgentConfig()
    recorder = TraceRecorder()
    started_at = datetime.now(UTC)
    result = run_agent(
        db=db,
        llm=llm,
        customer_id=request.customer_id,
        message=request.message,
        config=config,
        recorder=recorder,
    )
    _save_trace(db, request, result, recorder, config, started_at)

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
    )


def _save_trace(
    db: DbSession,
    request: AgentRunRequest,
    result: AgentRunResult,
    recorder: TraceRecorder,
    config: AgentConfig,
    started_at: datetime,
) -> None:
    # The customer's request has already been acted on; a failure to store its trace is
    # logged rather than turned into an error response.
    try:
        db.add(
            build_trace(
                execution_id=result.execution_id,
                source="api",
                customer_id=request.customer_id,
                model=config.model,
                agent_version="default",
                status=result.status,
                escalation_reason=result.escalation_reason,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                turns=[
                    {
                        "message": request.message,
                        "status": result.status,
                        "final_response": result.final_response,
                        "escalation_reason": result.escalation_reason,
                        "intent": result.intent,
                        "latency_ms": result.latency_ms,
                    }
                ],
                messages=result.messages,
                tool_calls=result.tool_calls,
                events=recorder.events,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not store the execution trace")
