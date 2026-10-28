import logging

import pytest

from app.agent.llm import LLMUsage
from app.observability.tracing import EventStatus, EventType, TraceRecorder


def test_events_are_numbered_in_start_order_and_tagged_with_the_turn() -> None:
    recorder = TraceRecorder()
    recorder.start_turn()
    with recorder.span(EventType.TOOL_CALL, tool_name="get_order"):
        recorder.point(EventType.TOOL_RETRY, tool_name="get_order", attempt=1)
    recorder.start_turn()
    recorder.point(EventType.RESPONSE_FINALIZED)

    assert [(e.sequence, e.type, e.turn) for e in recorder.events] == [
        (1, EventType.TOOL_CALL, 1),
        (2, EventType.TOOL_RETRY, 1),
        (3, EventType.RESPONSE_FINALIZED, 2),
    ]


def test_span_measures_duration_and_point_has_none() -> None:
    recorder = TraceRecorder()
    with recorder.span(EventType.MODEL_CALL) as span:
        span.set_usage("claude-opus-5", LLMUsage(input_tokens=10, output_tokens=2))
    point = recorder.point(EventType.TOOL_SELECTED)

    assert span.duration_ms is not None and span.duration_ms >= 0
    assert (span.model, span.input_tokens, span.output_tokens) == ("claude-opus-5", 10, 2)
    assert point.duration_ms is None


def test_exception_in_span_is_recorded_redacted_and_reraised() -> None:
    recorder = TraceRecorder()

    with pytest.raises(ConnectionError), recorder.span(EventType.TOOL_CALL):
        raise ConnectionError("postgresql://arl:hunter2@db/x unreachable")

    [event] = recorder.events
    assert event.status == EventStatus.ERROR
    assert event.error_type == "ConnectionError"
    assert event.error_message == "postgresql://arl:***@db/x unreachable"
    assert event.duration_ms is not None


def test_long_error_messages_are_truncated() -> None:
    event = TraceRecorder().point(EventType.TOOL_RETRY, error=RuntimeError("x" * 2000))

    assert event.error_message is not None and len(event.error_message) == 500


def test_point_with_error_keeps_a_non_default_status() -> None:
    retry = TraceRecorder().point(
        EventType.TOOL_RETRY, status=EventStatus.RETRY, error=TimeoutError("slow")
    )

    assert (retry.status, retry.error_type) == (EventStatus.RETRY, "TimeoutError")


def test_resuming_a_recorder_continues_sequence_and_turn() -> None:
    first = TraceRecorder()
    first.start_turn()
    first.start_turn()
    first.point(EventType.RESPONSE_FINALIZED)

    resumed = TraceRecorder(first.execution_id, first.events)
    resumed.point(EventType.EVALUATION)

    assert resumed.execution_id == first.execution_id
    assert [(e.sequence, e.turn) for e in first.events] == [(1, 2), (2, 2)]


def test_every_event_is_logged_with_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    recorder = TraceRecorder()
    with caplog.at_level(logging.INFO, logger="app.trace"):
        recorder.point(
            EventType.TOOL_RETRY,
            tool_name="cancel_order",
            attempt=2,
            status=EventStatus.RETRY,
            error=TimeoutError("timed out"),
        )

    [log] = caplog.records
    assert log.levelno == logging.WARNING
    assert log.getMessage() == "tool_retry"
    assert (log.tool, log.attempt, log.error_type) == ("cancel_order", 2, "TimeoutError")  # type: ignore[attr-defined]
