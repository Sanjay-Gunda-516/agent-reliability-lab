"""Creating and executing test runs."""

import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.config import AgentConfig
from app.agent.llm import LLMClient
from app.db.lab_models import ScenarioRecord, TestRunRecord
from app.evaluation.evaluate import EvaluatedVariant, evaluate_variant
from app.evaluation.judge import Judge
from app.evaluation.metrics import compute_metrics
from app.evaluation.store import load_summaries, save_result
from app.observability.context import correlate, current
from app.scenarios.loader import load_scenarios
from app.scenarios.runner import run_scenario, select_variants
from app.scenarios.schema import Persona, Scenario, Tag, variant_id
from app.scenarios.sync import sync_scenarios

logger = logging.getLogger(__name__)


class RunSelection(BaseModel):
    """Which variants to run. Empty filters mean 'all'."""

    scenario_ids: list[str] = Field(default_factory=list)
    variant_ids: list[str] = Field(
        default_factory=list, description="e.g. 'cancel-eligible-order:normal'"
    )
    tags: list[Tag] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)


class SelectionError(ValueError):
    pass


def _cases(scenarios: list[Scenario], selection: RunSelection) -> list[tuple[Scenario, Persona]]:
    unknown = set(selection.scenario_ids) - {s.id for s in scenarios}
    if unknown:
        raise SelectionError(f"unknown scenario id(s): {sorted(unknown)}")
    known_variants = {variant_id(s.id, p) for s in scenarios for p in s.variants}
    unknown = set(selection.variant_ids) - known_variants
    if unknown:
        raise SelectionError(f"unknown variant id(s): {sorted(unknown)}")
    cases = select_variants(
        scenarios,
        scenario_ids=set(selection.scenario_ids) or None,
        tags=set(selection.tags) or None,
        personas=set(selection.personas) or None,
    )
    if selection.variant_ids:
        cases = [(s, p) for s, p in cases if variant_id(s.id, p) in selection.variant_ids]
    return cases


def preview_cases(selection: RunSelection) -> list[tuple[Scenario, Persona]]:
    """The variants a selection would run, from the current scenario files."""
    return _cases(load_scenarios(), selection)


def create_test_run(
    db: Session,
    selection: RunSelection,
    *,
    config: AgentConfig,
    judge_model: str | None,
    agent_version: str = "default",
) -> TestRunRecord:
    # Sync first so the run is evaluated against exactly the scenario versions stored now.
    scenarios = load_scenarios()
    sync_scenarios(db, scenarios)
    cases = _cases(scenarios, selection)
    if not cases:
        raise SelectionError("the selection matches no scenario variants")

    run = TestRunRecord(
        status="queued",
        request_id=current().request_id,
        agent_version=agent_version,
        agent_config=dataclasses.asdict(config),
        judge_model=judge_model,
        selection=selection.model_dump(mode="json"),
        variant_count=len(cases),
    )
    db.add(run)
    db.commit()
    return run


def _stored_scenarios(db: Session) -> list[Scenario]:
    records = db.scalars(select(ScenarioRecord).where(ScenarioRecord.is_active))
    return [Scenario.model_validate(r.definition) for r in records]


def execute_test_run(
    test_run_id: int,
    *,
    session_factory: sessionmaker[Session],
    llm: LLMClient,
    judge: Judge | None,
    engine: Engine | None = None,
    on_result: Callable[[EvaluatedVariant], None] | None = None,
) -> None:
    with correlate(test_run_id=test_run_id), session_factory() as db:
        run = db.get(TestRunRecord, test_run_id)
        if run is None:
            raise ValueError(f"test run {test_run_id} not found")
        run.status = "running"
        run.started_at = datetime.now(UTC)
        db.commit()

        try:
            config = AgentConfig(**run.agent_config)
            cases = _cases(_stored_scenarios(db), RunSelection.model_validate(run.selection))
            for scenario, persona in cases:
                scenario_run = run_scenario(
                    scenario, persona, llm=llm, config=config, engine=engine
                )
                evaluated = evaluate_variant(scenario, scenario_run, judge)
                save_result(
                    db, run.id, evaluated, agent_version=run.agent_version, model=config.model
                )
                db.commit()  # keep finished variants even if a later one fails
                if on_result:
                    on_result(evaluated)
            run.metrics = compute_metrics(load_summaries(db, run.id)).model_dump(mode="json")
            run.status = "completed"
        except Exception as exc:
            logger.exception("Test run %s failed", test_run_id)
            db.rollback()
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished_at = datetime.now(UTC)
            db.commit()
