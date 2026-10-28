from typing import Any

import pytest

from app.agent.graph import escalate, finalize, route_after_agent, route_after_tools
from app.agent.state import AgentState, EscalationReason
from app.agent.tool_registry import TOOLS, anthropic_tool_definitions

ASSISTANT_TOOL_TURN: dict[str, Any] = {
    "role": "assistant",
    "content": [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_1", "name": "get_order", "input": {"order_id": 1}},
    ],
}
ASSISTANT_TEXT_TURN: dict[str, Any] = {
    "role": "assistant",
    "content": [{"type": "text", "text": "Order #1 has shipped."}],
}


def state(**values: Any) -> AgentState:
    return AgentState(**values)  # type: ignore[typeddict-item]


class TestToolDefinitions:
    def test_every_registered_tool_is_offered_to_claude(self) -> None:
        assert [t["name"] for t in anthropic_tool_definitions()] == list(TOOLS)

    @pytest.mark.parametrize("definition", anthropic_tool_definitions(), ids=lambda d: d["name"])
    def test_schemas_are_self_contained_and_closed(self, definition: dict[str, Any]) -> None:
        schema = definition["input_schema"]

        assert "$defs" not in schema and "$ref" not in str(schema)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert definition["description"]

    def test_no_tool_accepts_a_customer_id(self) -> None:
        for definition in anthropic_tool_definitions():
            assert "customer_id" not in definition["input_schema"]["properties"]


class TestRouting:
    def test_tool_use_routes_to_tools(self) -> None:
        assert route_after_agent(state(messages=[ASSISTANT_TOOL_TURN])) == "run_tools"

    def test_plain_reply_routes_to_finalize(self) -> None:
        assert route_after_agent(state(messages=[ASSISTANT_TEXT_TURN])) == "finalize"

    def test_escalation_wins_over_pending_tools(self) -> None:
        current = state(
            messages=[ASSISTANT_TOOL_TURN], escalation_reason=EscalationReason.TOOL_CALL_LIMIT
        )
        assert route_after_agent(current) == "escalate"

    def test_after_tools_loops_back_unless_escalating(self) -> None:
        assert route_after_tools(state()) == "agent"
        reason = EscalationReason.APPROVAL_REQUIRED
        assert route_after_tools(state(escalation_reason=reason)) == "escalate"


class TestTerminalNodes:
    def test_finalize_uses_last_reply_text(self) -> None:
        result = finalize(state(messages=[ASSISTANT_TEXT_TURN]))

        assert result == {"status": "completed", "final_response": "Order #1 has shipped."}

    def test_finalize_escalates_an_empty_reply(self) -> None:
        result = finalize(state(messages=[{"role": "assistant", "content": []}]))

        assert result["status"] == "escalated"
        assert result["escalation_reason"] == EscalationReason.EMPTY_RESPONSE

    def test_escalate_closes_unanswered_tool_calls(self) -> None:
        current = state(
            messages=[ASSISTANT_TOOL_TURN], escalation_reason=EscalationReason.TOOL_CALL_LIMIT
        )

        result = escalate(current)

        assert result["status"] == "escalated"
        [closing] = result["messages"]
        assert closing["role"] == "user"
        assert closing["content"][0]["tool_use_id"] == "toolu_1"
        assert closing["content"][0]["is_error"] is True
