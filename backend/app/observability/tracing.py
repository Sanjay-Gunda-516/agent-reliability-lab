"""Step-level trace events for one agent execution.

Timed steps (Claude calls, tool calls, evaluation, judge) are spans: one event with a
start time and duration. Instantaneous things (a tool being selected, a retry, a rule
rejection) are point events. Each event is also written to the log, so logs and stored
traces come from the same place.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.observability.context import new_id
from app.observability.logging import redact

logger = logging.getLogger("app.trace")

MAX_ERROR_CHARS = 500


class EventType(StrEnum):
    REQUEST_RECEIVED = "request_received"
    INTENT = "intent"
    MODEL_CALL = "model_call"
    TOOL_SELECTED = "tool_selected"
    TOOL_CALL = "tool_call"
    TOOL_RETRY = "tool_retry"
    RULE_REJECTION = "rule_rejection"
    ESCALATION = "escalation"
    RESPONSE_FINALIZED = "response_finalized"
    EVALUATION = "evaluation"
    JUDGE = "judge"


class EventStatus(StrEnum):
    OK = "ok"
    REJECTED = "rejected"  # a business rule said no; the system worked as designed
    RETRY = "retry"
    ERROR = "error"


@dataclass
class TraceEvent:
    sequence: int
    type: EventType
    started_at: datetime
    turn: int
    node: str | None = None
    status: EventStatus = EventStatus.OK
    duration_ms: float | None = None  # None for point events
    tool_name: str | None = None
    attempt: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def fail(self, error: BaseException | str, status: EventStatus = EventStatus.ERROR) -> None:
        self.status = status
        if isinstance(error, BaseException):
            self.error_type = type(error).__name__
            message = str(error)
        else:
            self.error_type, message = error, None
        self.error_message = redact(message)[:MAX_ERROR_CHARS] if message else None

    def set_usage(self, model: str, usage: Any) -> None:
        self.model = model
        self.input_tokens = usage.input_tokens
        self.output_tokens = usage.output_tokens
        self.cache_creation_input_tokens = usage.cache_creation_input_tokens
        self.cache_read_input_tokens = usage.cache_read_input_tokens


class TraceRecorder:
    """Collects the events of one agent execution (all of its turns), in start order."""

    def __init__(self, execution_id: str | None = None, events: list[TraceEvent] | None = None):
        self.execution_id = execution_id or new_id()
        self.events: list[TraceEvent] = events if events is not None else []
        self.turn = max((e.turn for e in self.events), default=0)

    def start_turn(self) -> int:
        self.turn += 1
        return self.turn

    def _new(self, type_: EventType, fields: dict[str, Any]) -> TraceEvent:
        event = TraceEvent(
            sequence=len(self.events) + 1,
            type=type_,
            started_at=datetime.now(UTC),
            turn=self.turn,
            **fields,
        )
        self.events.append(event)
        return event

    def point(
        self, type_: EventType, *, error: BaseException | str | None = None, **fields: Any
    ) -> TraceEvent:
        event = self._new(type_, fields)
        if error is not None:
            event.fail(error, event.status if event.status != EventStatus.OK else EventStatus.ERROR)
        _log(event)
        return event

    @contextmanager
    def span(self, type_: EventType, **fields: Any) -> Iterator[TraceEvent]:
        event = self._new(type_, fields)
        started = time.perf_counter()
        try:
            yield event
        except Exception as exc:
            event.fail(exc)
            raise
        finally:
            event.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            _log(event)


def _log(event: TraceEvent) -> None:
    level = {
        EventStatus.ERROR: logging.WARNING,
        EventStatus.RETRY: logging.WARNING,
    }.get(event.status, logging.INFO)
    fields = {
        k: v
        for k, v in {
            "event": str(event.type),
            "turn": event.turn,
            "node": event.node,
            "status": str(event.status),
            "duration_ms": event.duration_ms,
            "tool": event.tool_name,
            "attempt": event.attempt,
            "model": event.model,
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "error_type": event.error_type,
            "error_message": event.error_message,
            **event.attributes,
        }.items()
        if v is not None
    }
    logger.log(level, str(event.type), extra=fields)
