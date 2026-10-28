"""Turning an execution's in-memory trace into database rows."""

import json
from datetime import datetime
from typing import Any

from app.agent.state import ToolCallRecord
from app.db.lab_models import ExecutionTraceRecord, ToolCallRow, TraceEventRecord
from app.observability.context import current
from app.observability.tracing import EventType, TraceEvent


def to_json(value: Any) -> Any:
    """Make enums, Decimals, datetimes and dataclass dicts JSON-safe for JSONB columns."""
    return json.loads(json.dumps(value, default=str))


def event_rows(events: list[TraceEvent]) -> list[TraceEventRecord]:
    return [
        TraceEventRecord(
            sequence=e.sequence,
            turn=e.turn,
            type=str(e.type),
            node=e.node,
            status=str(e.status),
            started_at=e.started_at,
            duration_ms=e.duration_ms,
            tool_name=e.tool_name,
            attempt=e.attempt,
            model=e.model,
            input_tokens=e.input_tokens,
            output_tokens=e.output_tokens,
            cache_creation_input_tokens=e.cache_creation_input_tokens,
            cache_read_input_tokens=e.cache_read_input_tokens,
            error_type=e.error_type,
            error_message=e.error_message,
            attributes=to_json(e.attributes),
        )
        for e in events
    ]


def tool_call_rows(calls: list[ToolCallRecord], events: list[TraceEvent]) -> list[ToolCallRow]:
    # A tool_use id can run twice (refused for approval, then executed once approved),
    # so each call takes the next span recorded for its id.
    spans: dict[str, list[TraceEvent]] = {}
    for event in events:
        if event.type == EventType.TOOL_CALL:
            spans.setdefault(event.attributes.get("tool_use_id", ""), []).append(event)
    rows = []
    for sequence, call in enumerate(calls, 1):
        queue = spans.get(call["tool_use_id"]) or []
        span = queue.pop(0) if queue else None
        rows.append(
            ToolCallRow(
                sequence=sequence,
                turn=span.turn if span else 0,
                tool_use_id=call["tool_use_id"],
                name=call["name"],
                arguments=to_json(call["input"]),
                status=call["status"],
                error=call["error"],
                attempts=call["attempts"],
                latency_ms=call["latency_ms"],
                output=to_json(call["output"]),
                started_at=span.started_at if span else None,
            )
        )
    return rows


def build_trace(
    *,
    execution_id: str,
    source: str,
    customer_id: int,
    model: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    turns: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tool_calls: list[ToolCallRecord],
    events: list[TraceEvent],
    escalation_reason: str | None = None,
    error: str | None = None,
    agent_version: str | None = None,
    test_run_id: int | None = None,
    scenario_id: str | None = None,
    variant_id: str | None = None,
    initial_state: dict[str, Any] | None = None,
    final_state: dict[str, Any] | None = None,
    injected_faults: list[dict[str, Any]] | None = None,
) -> ExecutionTraceRecord:
    return ExecutionTraceRecord(
        execution_id=execution_id,
        source=source,
        request_id=current().request_id,
        test_run_id=test_run_id,
        scenario_id=scenario_id,
        variant_id=variant_id,
        customer_id=customer_id,
        agent_version=agent_version,
        model=model,
        status=status,
        escalation_reason=escalation_reason,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
        turns=to_json(turns),
        messages=to_json(messages),
        initial_state=initial_state,
        final_state=final_state,
        injected_faults=injected_faults,
        events=event_rows(events),
        tool_calls=tool_call_rows(tool_calls, events),
    )
