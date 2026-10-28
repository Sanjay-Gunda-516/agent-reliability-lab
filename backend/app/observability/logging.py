"""Structured logging: JSON for machines (CloudWatch, CI), key=value text for people.

Every record carries the current correlation ids, and secrets are masked before
anything is written.
"""

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import BACKEND_DIR
from app.observability.context import current

_SECRETS = [
    # Anthropic and LangSmith API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-***"),
    (re.compile(r"lsv2_[A-Za-z0-9_\-]{8,}"), "lsv2_***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer ***"),
    # Password in a connection URL: scheme://user:password@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(r"(?i)\b(api[_-]?key|password|secret|access[_-]?token)(\s*[=:]\s*)[^\s,;&]+"),
        r"\1\2***",
    ),
]

# Attributes every LogRecord has; anything else came from `extra=` and is logged.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "correlation",
}

_base_record_factory = logging.getLogRecordFactory()


def _correlated_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    # Capture ids when the record is created: a handler may format it later, after the
    # request or run that produced it has finished.
    record = _base_record_factory(*args, **kwargs)
    record.correlation = current().fields()
    return record


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, replacement in _SECRETS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    correlation = getattr(record, "correlation", None)
    extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
    return {**(correlation if correlation is not None else current().fields()), **extras}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(redact(payload), default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        time = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        context = " ".join(f"{k}={v}" for k, v in _fields(record).items())
        line = f"{time} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if context:
            line += f"  [{context}]"
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return str(redact(line))


class LoggingSettings(BaseSettings):
    """Separate from app Settings so logging works even before DATABASE_URL is set."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"


class _AppHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marks the handler we own, so reconfiguring replaces it without touching others."""


def configure_logging(settings: LoggingSettings | None = None) -> None:
    settings = settings or LoggingSettings()
    logging.setLogRecordFactory(_correlated_record_factory)
    handler = _AppHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, _AppHandler)] + [handler]
    root.setLevel(settings.log_level)
    # Library chatter (HTTP connection details) only when something goes wrong.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "langsmith", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # we log requests ourselves
