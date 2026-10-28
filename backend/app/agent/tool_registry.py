"""The tools Claude may call, described from the same Pydantic models that validate them."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.tools import orders
from app.tools.schemas import (
    CreateReturnInput,
    NoInput,
    OrderLookupInput,
    PolicyInput,
    ToolContext,
    ToolInput,
)

Handler = Callable[[Session, ToolContext, Any], BaseModel]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: Handler


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_customer",
        description="Get the signed-in customer's profile (name and email).",
        input_model=NoInput,
        handler=lambda db, ctx, _: orders.get_customer(db, ctx),
    ),
    ToolSpec(
        name="list_orders",
        description=(
            "List the signed-in customer's orders, newest first, with status, total and "
            "product names. Use it to work out which order the customer means when they "
            "don't give an order number."
        ),
        input_model=NoInput,
        handler=lambda db, ctx, _: orders.list_orders(db, ctx),
    ),
    ToolSpec(
        name="get_order",
        description=(
            "Get full details for one of the customer's orders: status, dates, line items "
            "and any return in progress."
        ),
        input_model=OrderLookupInput,
        handler=orders.get_order,
    ),
    ToolSpec(
        name="cancel_order",
        description=(
            "Cancel one of the customer's orders. Only call this when the customer has "
            "clearly asked to cancel that specific order. Eligibility is checked by the "
            "system; if the order can't be cancelled the result explains why."
        ),
        input_model=OrderLookupInput,
        handler=orders.cancel_order,
    ),
    ToolSpec(
        name="check_refund_eligibility",
        description=(
            "Check whether an order can be returned for a refund, the refund amount, and "
            "whether the refund needs human review. Read-only."
        ),
        input_model=OrderLookupInput,
        handler=orders.check_refund_eligibility,
    ),
    ToolSpec(
        name="create_return",
        description=(
            "Start a return for a delivered order, refunding the order total. Only call "
            "this when the customer has clearly asked to return that specific order and "
            "given a reason. Eligibility is checked by the system."
        ),
        input_model=CreateReturnInput,
        handler=orders.create_return,
    ),
    ToolSpec(
        name="get_policy",
        description=(
            "Get the store's official cancellation, returns or refunds policy. Use this "
            "instead of answering policy questions from memory."
        ),
        input_model=PolicyInput,
        handler=lambda _db, _ctx, args: orders.get_policy(args),
    ),
)

TOOLS: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve Pydantic's `$defs`/`$ref` so each tool schema is self-contained."""
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def input_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = _inline_refs(model.model_json_schema())
    schema.setdefault("properties", {})
    return schema


def anthropic_tool_definitions() -> list[dict[str, Any]]:
    # Fixed order keeps the request prefix stable for prompt caching.
    return [
        {"name": s.name, "description": s.description, "input_schema": input_schema(s.input_model)}
        for s in TOOL_SPECS
    ]
