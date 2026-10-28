import copy
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.scenarios.loader import ScenarioLoadError, load_scenarios
from app.scenarios.schema import OrderFixture, Persona, Scenario

BASE: dict[str, Any] = {
    "id": "cancel-eligible-order",
    "title": "Cancel an unshipped order",
    "tags": ["happy-path"],
    "customer_id": 1001,
    "variants": {"normal": "Please cancel order #4821.", "angry": "CANCEL 4821 NOW"},
    "expected": {
        "behavior": "Cancels order 4821 and confirms it.",
        "outcome": "order_cancelled",
        "final_status": "completed",
        "intent": "cancel_order",
        "must_call": [{"tool": "cancel_order", "args": {"order_id": 4821}}],
        "state": {"orders": {4821: "cancelled"}},
    },
}


def build(**changes: Any) -> Scenario:
    data = copy.deepcopy(BASE)
    for dotted, value in changes.items():
        target = data
        *parents, leaf = dotted.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return Scenario.model_validate(data)


def test_valid_scenario_parses_with_single_turn_shorthand() -> None:
    scenario = build()

    assert scenario.variants[Persona.NORMAL] == ["Please cancel order #4821."]
    assert scenario.expected.must_call[0].result == "success"
    assert scenario.expected.must_not_call == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"expected.must_call": [{"tool": "refund_everything"}]}, "unknown tool"),
        ({"expected.must_call": [{"tool": "get_order", "args": {"id": 1}}]}, "no argument"),
        ({"expected.typo_field": 1}, "Extra inputs"),
        ({"expected.final_status": "escalated"}, "escalation_reason"),
        ({"expected.state": {"orders": {4821: "processing"}}}, "order_cancelled"),
        ({"id": "Not A Slug"}, "pattern"),
        ({"variants": {"normal": ["first", "second"]}}, "multi-turn"),
        ({"variants": {"robot": "beep"}}, "Input should be"),
        ({"faults": [{"type": "database_error", "tool": "get_order"}]}, "failure-recovery"),
    ],
)
def test_invalid_scenarios_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        build(**changes)


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ({"type": "database_error"}, "need a tool"),
        ({"type": "llm_unavailable", "tool": "get_order"}, "don't target a tool"),
        ({"type": "tool_timeout", "tool": "nope"}, "unknown tool"),
        ({"type": "tool_timeout", "tool": "get_order", "times": 0}, "greater than 0"),
    ],
)
def test_invalid_faults_are_rejected(fault: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        build(tags=["failure-recovery"], faults=[fault])


def test_durations_in_order_fixtures() -> None:
    fixture = OrderFixture.model_validate(
        {
            "id": 4900,
            "customer_id": 1001,
            "status": "processing",
            "items": [{"product_name": "Mug", "unit_price": "12.00"}],
            "placed_ago": "23h",
            "shipped_ago": "30m",
        }
    )

    assert fixture.placed_ago == timedelta(hours=23)
    assert fixture.shipped_ago == timedelta(minutes=30)
    with pytest.raises(ValidationError, match="duration"):
        OrderFixture.model_validate({**fixture.model_dump(), "placed_ago": "yesterday"})


def test_content_hash_tracks_any_change() -> None:
    original = build()

    assert original.content_hash() == build().content_hash()
    assert original.content_hash() != build(title="Different title").content_hash()
    assert (
        original.content_hash()
        != build(**{"expected.behavior": "Cancels it and says thanks."}).content_hash()
    )


def write(path: Path, text: str) -> None:
    path.write_text(text)


def test_loader_reads_every_yaml_file(tmp_path: Path) -> None:
    write(
        tmp_path / "a.yaml",
        """
- id: status-check
  title: Order status
  tags: [happy-path]
  customer_id: 1001
  variants: {normal: "Where is order #8821?"}
  expected:
    behavior: Looks up the order and reports that it has shipped.
    outcome: information_provided
    final_status: completed
""",
    )

    [scenario] = load_scenarios(tmp_path)

    assert scenario.id == "status-check"


def test_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    body = """
- id: same
  title: t
  tags: [happy-path]
  customer_id: 1001
  variants: {normal: hi}
  expected:
    behavior: Greets the customer back.
    outcome: information_provided
    final_status: completed
"""
    write(tmp_path / "a.yaml", body)
    write(tmp_path / "b.yaml", body)

    with pytest.raises(ScenarioLoadError, match="duplicate scenario id 'same'"):
        load_scenarios(tmp_path)


def test_loader_reports_file_and_scenario_on_error(tmp_path: Path) -> None:
    write(tmp_path / "bad.yaml", "- id: broken\n  title: t\n")

    with pytest.raises(ScenarioLoadError, match=r"bad.yaml broken"):
        load_scenarios(tmp_path)
