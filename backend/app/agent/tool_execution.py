"""Runs one tool call from Claude: validate, guard, execute with retries, record."""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agent.config import AgentConfig
from app.agent.state import ToolCallRecord, ToolCallStatus
from app.agent.tool_registry import TOOLS, ToolSpec
from app.observability.tracing import EventStatus, EventType, TraceEvent, TraceRecorder
from app.tools.schemas import ToolContext, ToolFailure, ToolInput

logger = logging.getLogger(__name__)

# Failures worth retrying: the request may succeed if tried again. Business rejections
# (ToolFailure results) and bugs are never retried.
RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (OperationalError, TimeoutError)


@dataclass(frozen=True)
class ToolExecution:
    record: ToolCallRecord
    result_block: dict[str, Any]


def call_signature(name: str, tool_input: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(tool_input, sort_keys=True)}"


def _run_with_retries(
    spec: ToolSpec,
    args: ToolInput,
    *,
    db: Session,
    tool_ctx: ToolContext,
    config: AgentConfig,
    sleep: Callable[[float], None],
    before_call: Callable[[str], None] | None,
    recorder: TraceRecorder,
) -> tuple[BaseModel, int]:
    for attempt in range(1, config.tool_max_attempts + 1):
        try:
            if before_call:
                before_call(spec.name)
            return spec.handler(db, tool_ctx, args), attempt
        except RETRYABLE_ERRORS as exc:
            db.rollback()
            recorder.point(
                EventType.TOOL_RETRY,
                node="run_tools",
                tool_name=spec.name,
                attempt=attempt,
                status=EventStatus.RETRY,
                error=exc,
            )
            logger.warning(
                "Tool %s failed (attempt %d/%d)",
                spec.name,
                attempt,
                config.tool_max_attempts,
                exc_info=True,
            )
            if attempt == config.tool_max_attempts:
                raise
            sleep(config.tool_retry_backoff_s * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def execute_tool_call(
    call: dict[str, Any],
    *,
    db: Session,
    tool_ctx: ToolContext,
    config: AgentConfig,
    previous_calls: list[ToolCallRecord],
    sleep: Callable[[float], None] = time.sleep,
    before_call: Callable[[str], None] | None = None,
    recorder: TraceRecorder | None = None,
) -> ToolExecution:
    """`before_call` runs before every attempt; scenarios use it to inject failures."""
    recorder = recorder or TraceRecorder()
    with recorder.span(
        EventType.TOOL_CALL,
        node="run_tools",
        tool_name=call["name"],
        attributes={"tool_use_id": call["id"]},
    ) as event:
        execution = _execute(
            call,
            db=db,
            tool_ctx=tool_ctx,
            config=config,
            previous_calls=previous_calls,
            sleep=sleep,
            before_call=before_call,
            recorder=recorder,
        )
        _annotate(event, execution.record, recorder)
    return execution


def _annotate(event: TraceEvent, record: ToolCallRecord, recorder: TraceRecorder) -> None:
    event.attempt = record["attempts"] or None
    if record["status"] == "rejected":
        event.status = EventStatus.REJECTED
        event.error_type = record["error"]
        reason = (record["output"] or {}).get("reason")
        recorder.point(
            EventType.RULE_REJECTION,
            node="run_tools",
            tool_name=record["name"],
            status=EventStatus.REJECTED,
            attributes={"error": record["error"], "reason": reason},
        )
    elif record["status"] == "error":
        event.fail(record["error"] or "tool_error")


def _execute(
    call: dict[str, Any],
    *,
    db: Session,
    tool_ctx: ToolContext,
    config: AgentConfig,
    previous_calls: list[ToolCallRecord],
    sleep: Callable[[float], None],
    before_call: Callable[[str], None] | None,
    recorder: TraceRecorder,
) -> ToolExecution:
    name: str = call["name"]
    tool_input: dict[str, Any] = call.get("input") or {}
    started = time.perf_counter()

    def finish(
        status: ToolCallStatus,
        content: str,
        *,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        attempts: int = 0,
    ) -> ToolExecution:
        record = ToolCallRecord(
            tool_use_id=call["id"],
            name=name,
            input=tool_input,
            status=status,
            output=output,
            error=error,
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": call["id"],
            "content": content,
        }
        if status == "error":
            block["is_error"] = True
        return ToolExecution(record, block)

    spec = TOOLS.get(name)
    if spec is None:
        return finish("error", f"Error: there is no tool named '{name}'.", error="unknown_tool")

    signature = call_signature(name, tool_input)
    repeats = sum(1 for r in previous_calls if call_signature(r["name"], r["input"]) == signature)
    if repeats >= config.max_identical_tool_calls:
        return finish(
            "error",
            "Error: this exact call was already made. Use the earlier result instead.",
            error="repeated_call",
        )

    try:
        args = spec.input_model.model_validate(tool_input)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or 'input'}: {e['msg']}"
            for e in exc.errors(include_url=False)
        )
        return finish("error", f"Error: invalid arguments - {problems}", error="invalid_arguments")

    try:
        result, attempts = _run_with_retries(
            spec,
            args,
            db=db,
            tool_ctx=tool_ctx,
            config=config,
            sleep=sleep,
            before_call=before_call,
            recorder=recorder,
        )
    except RETRYABLE_ERRORS:
        return finish(
            "error",
            "Error: the order system is temporarily unavailable, so this request could not "
            "be completed right now.",
            error="tool_unavailable",
            attempts=config.tool_max_attempts,
        )
    except Exception:
        db.rollback()
        logger.exception("Tool %s raised unexpectedly", name)
        return finish(
            "error",
            "Error: something went wrong while running this tool.",
            error="internal_error",
            attempts=1,
        )

    output = result.model_dump(mode="json")
    if isinstance(result, ToolFailure):
        return finish(
            "rejected",
            result.model_dump_json(),
            output=output,
            error=result.error,
            attempts=attempts,
        )
    return finish("success", result.model_dump_json(), output=output, attempts=attempts)
