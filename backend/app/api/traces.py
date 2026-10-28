from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.lab_models import ExecutionTraceRecord, ToolCallRow, TraceEventRecord
from app.db.session import DbSession

router = APIRouter(prefix="/traces", tags=["traces"])

AGENT_MODEL_EVENTS = ("intent", "model_call")


class TraceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    execution_id: str
    source: str
    request_id: str | None
    test_run_id: int | None
    scenario_id: str | None
    variant_id: str | None
    customer_id: int
    agent_version: str | None
    model: str
    status: str
    escalation_reason: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class Correlation(BaseModel):
    execution_id: str
    request_id: str | None
    test_run_id: int | None
    evaluation_result_id: int | None
    scenario_id: str | None
    variant_id: str | None


class Timing(BaseModel):
    total_ms: float | None
    model_ms: float
    tool_ms: float
    evaluation_ms: float


class Tokens(BaseModel):
    calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class Counts(BaseModel):
    events: int
    turns: int
    tool_calls: int
    retries: int
    errors: int
    rule_rejections: int


class TraceDetail(TraceSummary):
    correlation: Correlation
    timing: Timing
    agent_tokens: Tokens
    judge_tokens: Tokens
    counts: Counts
    turns: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    initial_state: dict[str, Any] | None
    final_state: dict[str, Any] | None
    injected_faults: list[dict[str, Any]] | None


class EventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    turn: int
    type: str
    node: str | None
    status: str
    started_at: datetime
    duration_ms: float | None
    tool_name: str | None
    attempt: int | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    error_type: str | None
    error_message: str | None
    attributes: dict[str, Any]


class ToolCallView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    turn: int
    tool_use_id: str
    name: str
    arguments: dict[str, Any]
    status: str
    error: str | None
    attempts: int
    latency_ms: float
    output: Any
    started_at: datetime | None


def _trace_or_404(db: Session, execution_id: str) -> ExecutionTraceRecord:
    trace = db.scalars(
        select(ExecutionTraceRecord).where(ExecutionTraceRecord.execution_id == execution_id)
    ).one_or_none()
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trace not found.")
    return trace


def _tokens(events: list[TraceEventRecord]) -> Tokens:
    return Tokens(
        calls=len(events),
        input_tokens=sum(e.input_tokens or 0 for e in events),
        output_tokens=sum(e.output_tokens or 0 for e in events),
        cache_creation_input_tokens=sum(e.cache_creation_input_tokens or 0 for e in events),
        cache_read_input_tokens=sum(e.cache_read_input_tokens or 0 for e in events),
    )


def _duration(events: list[TraceEventRecord], *types: str) -> float:
    return round(sum(e.duration_ms or 0 for e in events if e.type in types), 3)


def summarize(trace: ExecutionTraceRecord) -> TraceDetail:
    events = trace.events
    total = (
        (trace.finished_at - trace.started_at).total_seconds() * 1000 if trace.finished_at else None
    )
    return TraceDetail.model_validate(
        {
            **TraceSummary.model_validate(trace).model_dump(),
            "correlation": Correlation(
                execution_id=trace.execution_id,
                request_id=trace.request_id,
                test_run_id=trace.test_run_id,
                evaluation_result_id=trace.result.id if trace.result else None,
                scenario_id=trace.scenario_id,
                variant_id=trace.variant_id,
            ),
            "timing": Timing(
                total_ms=round(total, 3) if total is not None else None,
                model_ms=_duration(events, *AGENT_MODEL_EVENTS),
                tool_ms=_duration(events, "tool_call"),
                evaluation_ms=_duration(events, "evaluation"),
            ),
            "agent_tokens": _tokens([e for e in events if e.type in AGENT_MODEL_EVENTS]),
            "judge_tokens": _tokens([e for e in events if e.type == "judge"]),
            "counts": Counts(
                events=len(events),
                turns=len(trace.turns),
                tool_calls=len(trace.tool_calls),
                retries=sum(e.type == "tool_retry" for e in events),
                errors=sum(e.status == "error" for e in events),
                rule_rejections=sum(e.type == "rule_rejection" for e in events),
            ),
            "turns": trace.turns,
            "messages": trace.messages,
            "initial_state": trace.initial_state,
            "final_state": trace.final_state,
            "injected_faults": trace.injected_faults,
        }
    )


@router.get("", response_model=list[TraceSummary])
def list_traces(
    db: DbSession,
    source: Annotated[str | None, Query(pattern="^(api|evaluation)$")] = None,
    test_run_id: int | None = None,
    trace_status: Annotated[
        str | None, Query(alias="status", pattern="^(completed|escalated|error)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ExecutionTraceRecord]:
    query = select(ExecutionTraceRecord)
    if source:
        query = query.where(ExecutionTraceRecord.source == source)
    if test_run_id is not None:
        query = query.where(ExecutionTraceRecord.test_run_id == test_run_id)
    if trace_status:
        query = query.where(ExecutionTraceRecord.status == trace_status)
    return list(db.scalars(query.order_by(ExecutionTraceRecord.id.desc()).limit(limit)))


@router.get("/{execution_id}", response_model=TraceDetail)
def get_trace(execution_id: str, db: DbSession) -> TraceDetail:
    return summarize(_trace_or_404(db, execution_id))


@router.get("/{execution_id}/events", response_model=list[EventView])
def list_events(
    execution_id: str,
    db: DbSession,
    event_type: Annotated[list[str] | None, Query(alias="type")] = None,
    event_status: Annotated[list[str] | None, Query(alias="status")] = None,
) -> list[TraceEventRecord]:
    trace = _trace_or_404(db, execution_id)
    query = select(TraceEventRecord).where(TraceEventRecord.execution_trace_id == trace.id)
    if event_type:
        query = query.where(TraceEventRecord.type.in_(event_type))
    if event_status:
        query = query.where(TraceEventRecord.status.in_(event_status))
    return list(db.scalars(query.order_by(TraceEventRecord.sequence)))


@router.get("/{execution_id}/tool-calls", response_model=list[ToolCallView])
def list_tool_calls(execution_id: str, db: DbSession) -> list[ToolCallRow]:
    return list(_trace_or_404(db, execution_id).tool_calls)
