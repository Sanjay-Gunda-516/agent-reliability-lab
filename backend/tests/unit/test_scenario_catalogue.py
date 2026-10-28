"""Checks the committed scenario files load and refer to things that exist."""

from collections import Counter
from datetime import UTC, datetime

import pytest

from app.db.models import Customer, Order
from app.scenarios.loader import load_scenarios
from app.scenarios.schema import Persona, Scenario, Tag
from app.seed import build_demo_data

SCENARIOS = load_scenarios()
DEMO = build_demo_data(datetime.now(UTC))
CUSTOMERS = {row.id for row in DEMO if isinstance(row, Customer)}
ORDERS = {row.id for row in DEMO if isinstance(row, Order)}


def test_catalogue_loads_and_covers_every_category() -> None:
    tags = Counter(tag for s in SCENARIOS for tag in s.tags)

    assert len(SCENARIOS) >= 30
    assert set(tags) == set(Tag)


def test_every_persona_is_used() -> None:
    assert {p for s in SCENARIOS for p in s.variants} == set(Persona)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_refers_to_real_customers_and_orders(scenario: Scenario) -> None:
    known_orders = ORDERS | {o.id for o in scenario.setup.orders}
    expected = scenario.expected
    referenced = (
        set(expected.state.orders)
        | set(expected.state.returns)
        | {
            call.args["order_id"]
            for call in [*expected.must_call, *expected.must_not_call]
            if call.args and "order_id" in call.args
        }
    )

    assert scenario.customer_id in CUSTOMERS
    assert referenced <= known_orders
    assert all(o.customer_id in CUSTOMERS for o in scenario.setup.orders)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_setup_orders_use_new_ids(scenario: Scenario) -> None:
    # Fixtures add orders; silently replacing a demo order would confuse other expectations.
    assert not {o.id for o in scenario.setup.orders} & ORDERS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_stored_definition_round_trips(scenario: Scenario) -> None:
    """The database stores model_dump(mode="json"); it must load back identically."""
    reloaded = Scenario.model_validate(scenario.model_dump(mode="json"))

    assert reloaded == scenario
    assert reloaded.content_hash() == scenario.content_hash()
