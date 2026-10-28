"""Cost estimates from recorded token usage.

List prices for the first-party Claude API in USD per million tokens (as of 2026-06-24).
Prompt-cache writes (5-minute TTL) cost 1.25x input and cache reads 0.1x input. These
are estimates for comparing runs; the Anthropic Console is the source of truth for bills.
"""

from collections.abc import Iterable
from decimal import Decimal

from app.agent.state import LLMCallRecord

PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-opus-4-8": (Decimal("5"), Decimal("25")),  # refusal fallback target
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
}
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
CACHE_READ_MULTIPLIER = Decimal("0.1")
MILLION = Decimal(1_000_000)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> Decimal | None:
    """None when the model's price isn't known (rather than guessing)."""
    if not any((input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens)):
        return Decimal(0)
    if model not in PRICES_PER_MTOK:
        return None
    input_price, output_price = PRICES_PER_MTOK[model]
    total = (
        input_tokens * input_price
        + cache_creation_input_tokens * input_price * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * input_price * CACHE_READ_MULTIPLIER
        + output_tokens * output_price
    )
    return total / MILLION


def cost_of_calls(calls: Iterable[LLMCallRecord]) -> Decimal | None:
    total = Decimal(0)
    for call in calls:
        cost = estimate_cost(
            call["model"],
            call["input_tokens"],
            call["output_tokens"],
            call["cache_creation_input_tokens"],
            call["cache_read_input_tokens"],
        )
        if cost is None:
            return None
        total += cost
    return total
