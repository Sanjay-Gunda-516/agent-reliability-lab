"""Correlation ids for the current request, test run and agent execution.

Set once at each entry point with `correlate(...)`; anything running inside (including
work FastAPI hands to its threadpool) sees the same ids, and every log line carries them.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from typing import Any

_VALID_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class Correlation:
    request_id: str | None = None
    test_run_id: int | None = None
    execution_id: str | None = None
    scenario_id: str | None = None
    variant_id: str | None = None

    def fields(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# Correlation is frozen, so sharing one default instance is safe.
_current: ContextVar[Correlation] = ContextVar("correlation", default=Correlation())  # noqa: B039


def current() -> Correlation:
    return _current.get()


@contextmanager
def correlate(**ids: Any) -> Iterator[Correlation]:
    """Add or override ids for the duration of the block."""
    token = _current.set(replace(current(), **ids))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def new_id() -> str:
    return uuid.uuid4().hex


def accept_request_id(header_value: str | None) -> str:
    """Reuse a caller's X-Request-ID only if it's a sane token; never trust it blindly."""
    if header_value and _VALID_EXTERNAL_ID.match(header_value):
        return header_value
    return new_id()
