import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from app.observability.context import accept_request_id, correlate, current
from app.observability.logging import (
    JsonFormatter,
    LoggingSettings,
    TextFormatter,
    configure_logging,
    redact,
)


def record(message: str, exc_info: bool = False, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord(
        {"name": "app.test", "levelname": "INFO", "levelno": 20, "msg": message, **extra}
    )
    if exc_info:
        try:
            raise ValueError("password=hunter2 failed")
        except ValueError:
            rec.exc_info = sys.exc_info()
    return rec


class TestCorrelation:
    def test_nested_ids_merge_and_reset(self) -> None:
        with correlate(request_id="r1"):
            with correlate(execution_id="e1"):
                assert current().fields() == {"request_id": "r1", "execution_id": "e1"}
            assert current().fields() == {"request_id": "r1"}
        assert current().fields() == {}

    @pytest.mark.parametrize("header", ["abc-123", "trace.1_A"])
    def test_sane_request_ids_are_reused(self, header: str) -> None:
        assert accept_request_id(header) == header

    @pytest.mark.parametrize("header", [None, "", "x" * 65, "has space", "<script>"])
    def test_untrusted_request_ids_are_replaced(self, header: str | None) -> None:
        generated = accept_request_id(header)

        assert generated != header and len(generated) == 32


class TestJsonFormatter:
    def test_includes_correlation_and_extra_fields(self) -> None:
        with correlate(request_id="r1", test_run_id=7):
            line = JsonFormatter().format(
                record("tool call finished", tool="get_order", attempts=2)
            )

        payload = json.loads(line)
        assert payload["message"] == "tool call finished"
        assert payload["level"] == "INFO" and payload["logger"] == "app.test"
        assert payload["request_id"] == "r1" and payload["test_run_id"] == 7
        assert payload["tool"] == "get_order" and payload["attempts"] == 2
        assert payload["timestamp"].endswith("+00:00")

    def test_exceptions_are_included_and_redacted(self) -> None:
        payload = json.loads(JsonFormatter().format(record("boom", exc_info=True)))

        assert "ValueError" in payload["exception"]
        assert "hunter2" not in payload["exception"]


def test_ids_are_captured_when_the_record_is_created() -> None:
    configure_logging(LoggingSettings(log_format="json"))
    with correlate(request_id="r-early"):
        rec = logging.getLogger("app.test").makeRecord(
            "app.test", logging.INFO, __file__, 1, "queued", None, None
        )

    # Formatted after the request finished, as a buffered or async handler would.
    assert json.loads(JsonFormatter().format(rec))["request_id"] == "r-early"


def test_configure_logging_keeps_other_handlers() -> None:
    other = logging.NullHandler()
    logging.getLogger().addHandler(other)
    try:
        configure_logging()
        configure_logging()

        handlers = logging.getLogger().handlers
        assert other in handlers
        assert sum(type(h).__name__ == "_AppHandler" for h in handlers) == 1
    finally:
        logging.getLogger().removeHandler(other)


class TestTextFormatter:
    def test_human_readable_with_context(self) -> None:
        with correlate(execution_id="e1"):
            line = TextFormatter().format(record("agent turn finished", status="completed"))

        assert "INFO    app.test: agent turn finished" in line
        assert "[execution_id=e1 status=completed]" in line


class TestRedaction:
    @pytest.mark.parametrize(
        ("raw", "leaked"),
        [
            ("key sk-ant-api03-AbCdEf123456789xyz used", "AbCdEf123456789xyz"),
            ("langsmith lsv2_pt_0123456789abcdef", "0123456789abcdef"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload", "eyJhbGciOiJIUzI1NiJ9"),
            ("postgresql+psycopg://arl:s3cretPass@localhost:5433/db", "s3cretPass"),
            ("api_key=abc123def password: hunter2", "hunter2"),
        ],
    )
    def test_secrets_are_masked(self, raw: str, leaked: str) -> None:
        assert leaked not in redact(raw)

    def test_redaction_reaches_extra_fields(self) -> None:
        line = JsonFormatter().format(record("connecting", url="postgres://u:topsecret@db/x"))

        assert "topsecret" not in line
        assert json.loads(line)["url"] == "postgres://u:***@db/x"

    def test_ordinary_text_is_untouched(self) -> None:
        text = "Order #4821 for alice@example.com cancelled, refund $89.99"

        assert redact(text) == text


class TestRequestMiddleware:
    def test_generates_and_returns_a_request_id(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="app.http"):
            response = client.get("/health")

        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 32
        [log] = [r for r in caplog.records if r.getMessage() == "request completed"]
        assert (log.path, log.status_code) == ("/health", 200)  # type: ignore[attr-defined]
        assert JsonFormatter().format(log).count(request_id) == 1

    def test_reuses_a_valid_incoming_request_id(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "from-gateway-42"})

        assert response.headers["X-Request-ID"] == "from-gateway-42"
