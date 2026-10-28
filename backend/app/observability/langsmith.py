"""Optional LangSmith tracing.

Off unless LANGSMITH_TRACING=true *and* an API key is set. It's switched on or off
explicitly around every run, so a stray LANGSMITH_* environment variable can't turn it
on by accident. Our own trace tables remain the record of what happened.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import anthropic
from langsmith import Client, tracing_context
from langsmith.wrappers import wrap_anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LangSmith:
    enabled: bool
    project: str
    client: Client | None = None
    reason: str = ""
    tags: list[str] = field(default_factory=list)


@lru_cache
def get_langsmith() -> LangSmith:
    settings = get_settings()
    project = settings.langsmith_project
    if not settings.langsmith_tracing:
        return LangSmith(False, project, reason="LANGSMITH_TRACING is off")
    if settings.langsmith_api_key is None:
        logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is missing; tracing is off")
        return LangSmith(False, project, reason="LANGSMITH_API_KEY is missing")
    hide = settings.langsmith_hide_content
    client = Client(
        api_key=settings.langsmith_api_key.get_secret_value(),
        api_url=settings.langsmith_endpoint,
        hide_inputs=hide,
        hide_outputs=hide,
    )
    logger.info("LangSmith tracing on", extra={"langsmith_project": project})
    return LangSmith(True, project, client)


@contextmanager
def langsmith_run(metadata: dict[str, Any], tags: list[str] | None = None) -> Iterator[None]:
    """Everything inside (LangGraph nodes, wrapped Claude calls) is traced, or explicitly not."""
    ls = get_langsmith()
    with tracing_context(
        enabled=ls.enabled,
        client=ls.client,
        project_name=ls.project,
        metadata={k: v for k, v in metadata.items() if v is not None},
        tags=tags or [],
    ):
        yield


def instrument(client: anthropic.Anthropic) -> anthropic.Anthropic:
    """Record Claude calls as LLM runs (with token usage) when LangSmith is on."""
    return wrap_anthropic(client) if get_langsmith().enabled else client
