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
) -> tuple[BaseModel, int]:
    for attempt in range(1, config.tool_max_attempts + 1):
        try:
            return spec.handler(db, tool_ctx, args), attempt
        except RETRYABLE_ERRORS:
            db.rollback()
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
            spec, args, db=db, tool_ctx=tool_ctx, config=config, sleep=sleep
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
