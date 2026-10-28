"""Runs scenarios through the real agent and collects what actually happened.

Each run happens inside one database transaction that is rolled back afterwards: the
demo store is reset, the conversation is played, the resulting state is captured, and
nothing persists. Grading the results is Phase 5's job; this module only observes.
"""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agent.checkpoints import get_checkpointer
from app.agent.config import AgentConfig
from app.agent.llm import LLMClient
from app.agent.service import run_agent
from app.agent.state import LLMCallRecord, ToolCallRecord
from app.db.models import Order, OrderItem, Return
from app.db.session import get_engine
from app.domain.enums import OrderStatus, ReturnStatus
from app.observability.context import correlate
from app.observability.tracing import TraceEvent, TraceRecorder
from app.scenarios.faults import InjectedFault, apply_faults
from app.scenarios.schema import OrderFixture, Persona, Scenario, Tag, variant_id
from app.seed import reset_demo_data

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    message: str
    status: str
    final_response: str
    escalation_reason: str | None
    intent: dict[str, Any] | None
    tool_calls: list[ToolCallRecord]
    llm_calls: list[LLMCallRecord]
    latency_ms: float


@dataclass(frozen=True)
class StateSnapshot:
    orders: dict[int, OrderStatus]
    returns: dict[int, ReturnStatus]  # order id -> status of its return
    owners: dict[int, int]  # order id -> customer id


@dataclass(frozen=True)
class ScenarioRun:
    scenario_id: str
    persona: Persona
    scenario_hash: str
    customer_id: int
    started_at: datetime
    latency_ms: float
    execution_id: str = ""
    events: list[TraceEvent] = field(default_factory=list)
    turns: list[TurnResult] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    initial_state: StateSnapshot | None = None
    final_state: StateSnapshot | None = None
    injected_faults: list[InjectedFault] = field(default_factory=list)
    error: str | None = None  # the run itself crashed

    @property
    def variant_id(self) -> str:
        return variant_id(self.scenario_id, self.persona)

    @property
    def status(self) -> str:
        if self.error or not self.turns:
            return "error"
        return self.turns[-1].status

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        return [call for turn in self.turns for call in turn.tool_calls]

    @property
    def llm_calls(self) -> list[LLMCallRecord]:
        return [call for turn in self.turns for call in turn.llm_calls]


def _fixture_order(fixture: OrderFixture, now: datetime) -> Order:
    items = [
        OrderItem(product_name=i.product_name, quantity=i.quantity, unit_price=i.unit_price)
        for i in fixture.items
    ]
    return Order(
        id=fixture.id,
        customer_id=fixture.customer_id,
        status=fixture.status,
        total_amount=sum((i.unit_price * i.quantity for i in items), Decimal("0.00")),
        placed_at=now - fixture.placed_ago,
        shipped_at=now - fixture.shipped_ago if fixture.shipped_ago else None,
        delivered_at=now - fixture.delivered_ago if fixture.delivered_ago else None,
        cancelled_at=now - fixture.cancelled_ago if fixture.cancelled_ago else None,
        items=items,
    )


def prepare_store(db: Session, scenario: Scenario, now: datetime) -> None:
    reset_demo_data(db, now)
    db.add_all(_fixture_order(fixture, now) for fixture in scenario.setup.orders)
    db.commit()


def snapshot_state(db: Session) -> StateSnapshot:
    db.expire_all()
    orders = list(db.scalars(select(Order)))
    return StateSnapshot(
        orders={order.id: order.status for order in orders},
        returns={ret.order_id: ret.status for ret in db.scalars(select(Return))},
        owners={order.id: order.customer_id for order in orders},
    )


def run_scenario(
    scenario: Scenario,
    persona: Persona,
    *,
    llm: LLMClient,
    config: AgentConfig | None = None,
    engine: Engine | None = None,
) -> ScenarioRun:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    run_llm, before_tool_call, fault_log = apply_faults(scenario.faults, llm)
    turns: list[TurnResult] = []
    messages: list[dict[str, Any]] = []
    initial_state: StateSnapshot | None = None
    final_state: StateSnapshot | None = None
    error: str | None = None
    recorder = TraceRecorder()

    with (
        correlate(
            scenario_id=scenario.id,
            variant_id=variant_id(scenario.id, persona),
            execution_id=recorder.execution_id,
        ),
        (engine or get_engine()).connect() as connection,
    ):
        transaction = connection.begin()
        # Commits made by the agent's tools become savepoints inside this transaction.
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            prepare_store(db, scenario, started_at)
            initial_state = snapshot_state(db)
            for message in scenario.variants[persona]:
                result = run_agent(
                    db=db,
                    llm=run_llm,
                    customer_id=scenario.customer_id,
                    message=message,
                    config=config,
                    before_tool_call=before_tool_call,
                    recorder=recorder,
                )
                messages = result.messages
                turns.append(
                    TurnResult(
                        message=message,
                        status=result.status,
                        final_response=result.final_response,
                        escalation_reason=result.escalation_reason,
                        intent=result.intent,
                        tool_calls=result.tool_calls,
                        llm_calls=result.llm_calls,
                        latency_ms=result.latency_ms,
                    )
                )
            final_state = snapshot_state(db)
        except Exception as exc:
            # A crash is a finding about the agent, not a reason to stop the suite.
            logger.exception("Scenario %s:%s crashed", scenario.id, persona)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            db.close()
            transaction.rollback()
            # Scenario runs are throwaway, like the store changes rolled back above.
            get_checkpointer().delete_thread(recorder.execution_id)

    return ScenarioRun(
        scenario_id=scenario.id,
        persona=persona,
        scenario_hash=scenario.content_hash(),
        customer_id=scenario.customer_id,
        started_at=started_at,
        latency_ms=(time.perf_counter() - started) * 1000,
        execution_id=recorder.execution_id,
        events=recorder.events,
        turns=turns,
        messages=messages,
        initial_state=initial_state,
        final_state=final_state,
        injected_faults=list(fault_log),
        error=error,
    )


def select_variants(
    scenarios: Iterable[Scenario],
    *,
    scenario_ids: set[str] | None = None,
    tags: set[Tag] | None = None,
    personas: set[Persona] | None = None,
) -> list[tuple[Scenario, Persona]]:
    selected = []
    for scenario in scenarios:
        if scenario_ids and scenario.id not in scenario_ids:
            continue
        if tags and not tags & set(scenario.tags):
            continue
        for persona in scenario.variants:
            if personas and persona not in personas:
                continue
            selected.append((scenario, persona))
    return selected


def run_suite(
    cases: list[tuple[Scenario, Persona]],
    *,
    llm: LLMClient,
    config: AgentConfig | None = None,
    engine: Engine | None = None,
    on_result: Callable[[ScenarioRun], None] | None = None,
) -> list[ScenarioRun]:
    runs = []
    for scenario, persona in cases:
        run = run_scenario(scenario, persona, llm=llm, config=config, engine=engine)
        runs.append(run)
        if on_result:
            on_result(run)
    return runs
