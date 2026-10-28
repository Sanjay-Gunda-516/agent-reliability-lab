from typing import Any

import pytest
from sqlalchemy import func, select

from app.agent.config import AgentConfig
from app.agent.llm import IntentClassification, LLMResponse
from app.agent.state import EscalationReason, Intent, IntentResult
from app.db.models import Order
from app.db.session import get_engine
from app.domain.enums import OrderStatus
from app.scenarios.faults import InjectedFault
from app.scenarios.loader import load_scenarios
from app.scenarios.runner import run_scenario, run_suite, select_variants
from app.scenarios.schema import FaultType, Persona, Scenario, Tag
from tests.fakes import USAGE, FakeLLM, calls, text, tool_use

pytestmark = pytest.mark.integration

FAST = AgentConfig(tool_retry_backoff_s=0)
CATALOGUE = {s.id: s for s in load_scenarios()}


def scenario(scenario_id: str) -> Scenario:
    return CATALOGUE[scenario_id]


def committed_order_count() -> int:
    with get_engine().connect() as conn:
        return conn.scalar(select(func.count()).select_from(Order)) or 0


class PoliteFake:
    """Answers every turn with text and never calls a tool."""

    def classify_intent(self, transcript: str, config: AgentConfig) -> IntentClassification:
        return IntentClassification(
            IntentResult(intent=Intent.OTHER), "fake", "end_turn", USAGE, 1.0
        )

    def respond(self, **_: Any) -> LLMResponse:
        return text("Thanks, I can help with that.")


def test_runs_a_scenario_and_rolls_everything_back() -> None:
    before = committed_order_count()
    llm = FakeLLM(
        [calls(tool_use("cancel_order", {"order_id": 4821})), text("Cancelled.")],
        intent=IntentResult(intent=Intent.CANCEL_ORDER, order_ids=[4821]),
    )

    run = run_scenario(scenario("cancel-eligible-order"), Persona.NORMAL, llm=llm, config=FAST)

    assert run.error is None
    assert run.status == "completed"
    assert run.variant_id == "cancel-eligible-order:normal"
    assert run.scenario_hash == scenario("cancel-eligible-order").content_hash()
    assert [c["name"] for c in run.tool_calls] == ["cancel_order"]
    assert run.initial_state is not None and run.final_state is not None
    assert run.initial_state.orders[4821] == OrderStatus.PROCESSING
    assert run.final_state.orders[4821] == OrderStatus.CANCELLED
    assert run.final_state.owners[9001] == 1002
    assert run.turns[0].intent == {"intent": "cancel_order", "order_ids": [4821]}
    assert committed_order_count() == before


def test_multi_turn_scenario_carries_history_between_turns() -> None:
    llm = FakeLLM(
        [
            text("Which one, #9001 or #9003?"),
            calls(tool_use("cancel_order", {"order_id": 9001})),
            text("Order #9001 is cancelled."),
        ]
    )

    run = run_scenario(scenario("clarify-then-cancel"), Persona.NORMAL, llm=llm, config=FAST)

    assert [t.final_response for t in run.turns] == [
        "Which one, #9001 or #9003?",
        "Order #9001 is cancelled.",
    ]
    second_turn_request = llm.requests[1]
    assert second_turn_request[1]["content"][0]["text"] == "Which one, #9001 or #9003?"
    assert run.final_state is not None
    assert run.final_state.orders[9001] == OrderStatus.CANCELLED
    assert run.final_state.orders[9003] == OrderStatus.PROCESSING


def test_setup_fixtures_are_added_to_the_store() -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 4900})), text("Done.")])

    run = run_scenario(scenario("cancel-just-inside-window"), Persona.NORMAL, llm=llm, config=FAST)

    assert run.tool_calls[0]["status"] == "success"
    assert run.final_state is not None
    assert run.final_state.orders[4900] == OrderStatus.CANCELLED


def test_transient_tool_fault_is_injected_then_recovered() -> None:
    llm = FakeLLM([calls(tool_use("cancel_order", {"order_id": 4821})), text("Cancelled.")])

    run = run_scenario(scenario("tool-timeout-then-recovery"), Persona.NORMAL, llm=llm, config=FAST)

    [call] = run.tool_calls
    assert (call["status"], call["attempts"]) == ("success", 3)
    assert run.injected_faults == [InjectedFault("cancel_order", FaultType.TOOL_TIMEOUT)] * 2


def test_persistent_tool_fault_leaves_the_order_untouched() -> None:
    llm = FakeLLM(
        [calls(tool_use("cancel_order", {"order_id": 4821})), text("Our system is down.")]
    )

    run = run_scenario(
        scenario("database-down-during-cancel"), Persona.NORMAL, llm=llm, config=FAST
    )

    assert run.tool_calls[0]["error"] == "tool_unavailable"
    assert run.final_state is not None
    assert run.final_state.orders[4821] == OrderStatus.PROCESSING
    assert len(run.injected_faults) == FAST.tool_max_attempts


def test_claude_outage_escalates() -> None:
    run = run_scenario(scenario("claude-outage"), Persona.NORMAL, llm=FakeLLM([]), config=FAST)

    assert run.status == "escalated"
    assert run.turns[0].escalation_reason == EscalationReason.LLM_UNAVAILABLE
    assert [f.target for f in run.injected_faults] == ["llm", "llm"]  # intent + agent call


def test_crashing_run_is_recorded_and_the_suite_continues() -> None:
    cases = [
        (scenario("cancel-eligible-order"), Persona.NORMAL),
        (scenario("order-status-shipped"), Persona.NORMAL),
    ]
    out_of_script = FakeLLM([])  # every agent call raises AssertionError

    runs = run_suite(cases, llm=out_of_script, config=FAST)

    assert [r.status for r in runs] == ["error", "error"]
    assert runs[0].error and "ran out of scripted responses" in runs[0].error


def test_entire_catalogue_executes_end_to_end() -> None:
    cases = select_variants(CATALOGUE.values())

    runs = run_suite(cases, llm=PoliteFake(), config=FAST)

    assert len(runs) == sum(len(s.variants) for s in CATALOGUE.values())
    assert [r.variant_id for r in runs if r.error] == []
    assert all(r.final_state is not None for r in runs)
    multi_turn = [r for r in runs if Tag.MULTI_TURN in CATALOGUE[r.scenario_id].tags]
    assert all(len(r.turns) == 2 for r in multi_turn)


def test_select_variants_filters() -> None:
    adversarial = select_variants(CATALOGUE.values(), tags={Tag.ADVERSARIAL})
    angry = select_variants(CATALOGUE.values(), personas={Persona.ANGRY})
    one = select_variants(CATALOGUE.values(), scenario_ids={"cancel-eligible-order"})

    assert all(Tag.ADVERSARIAL in s.tags for s, _ in adversarial)
    assert {p for _, p in angry} == {Persona.ANGRY}
    assert [p for _, p in one] == [Persona.NORMAL, Persona.ANGRY, Persona.CONFUSED]
