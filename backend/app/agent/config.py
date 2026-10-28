from dataclasses import dataclass
from typing import Literal

Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class AgentConfig:
    """Everything that defines how the agent behaves. Becomes an agent version in Phase 8."""

    model: str = "claude-opus-5"
    effort: Effort = "medium"
    intent_effort: Effort = "low"
    prompt_version: str = "v1"
    max_tokens: int = 16000

    # Loop protection
    max_agent_turns: int = 8
    max_tool_calls: int = 12
    max_identical_tool_calls: int = 2

    # Retries for infrastructure failures inside tools (not business rejections)
    tool_max_attempts: int = 3
    tool_retry_backoff_s: float = 0.2
