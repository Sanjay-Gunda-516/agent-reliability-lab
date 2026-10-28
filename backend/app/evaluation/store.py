"""Saving evaluated variants and reading them back for metrics."""

import dataclasses
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.lab_models import (
    EvaluationCheckRecord,
    EvaluationResultRecord,
    ExecutionTraceRecord,
)
from app.evaluation.evaluate import EvaluatedVariant
from app.evaluation.metrics import TokenTotals, VariantSummary
from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict
from app.scenarios.runner import StateSnapshot


def _json(value: Any) -> Any:
    """Make enums, Decimals and dataclasses JSON-safe for JSONB columns."""
    return json.loads(json.dumps(value, default=str))


def _state(snapshot: StateSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    state: dict[str, Any] = _json(
        {
            "orders": {str(k): v for k, v in snapshot.orders.items()},
            "returns": {str(k): v for k, v in snapshot.returns.items()},
            "owners": {str(k): v for k, v in snapshot.owners.items()},
        }
    )
    return state


def _failures(checks: list[CheckResult], severity: Severity) -> int:
    return sum(
        not c.passed and c.severity == severity and c.source == CheckSource.DETERMINISTIC
        for c in checks
    )


def save_result(
    db: Session, test_run_id: int, evaluated: EvaluatedVariant
) -> EvaluationResultRecord:
    run, summary, judge = evaluated.run, evaluated.summary, evaluated.judge_result
    last = run.turns[-1] if run.turns else None
    first_intent = run.turns[0].intent if run.turns else None
    record = EvaluationResultRecord(
        test_run_id=test_run_id,
        scenario_id=run.scenario_id,
        variant_id=run.variant_id,
        persona=str(run.persona),
        scenario_hash=run.scenario_hash,
        verdict=str(evaluated.verdict),
        critical_failures=_failures(evaluated.checks, Severity.CRITICAL),
        major_failures=_failures(evaluated.checks, Severity.MAJOR),
        minor_failures=_failures(evaluated.checks, Severity.MINOR),
        final_status=last.status if last else None,
        escalation_reason=last.escalation_reason if last else None,
        intent=first_intent["intent"] if first_intent else None,
        final_response=last.final_response if last else None,
        error=run.error,
        models=sorted({c["model"] for c in run.llm_calls}),
        latency_ms=run.latency_ms,
        llm_calls=summary.tokens.llm_calls,
        input_tokens=summary.tokens.input_tokens,
        output_tokens=summary.tokens.output_tokens,
        cache_creation_input_tokens=summary.tokens.cache_creation_input_tokens,
        cache_read_input_tokens=summary.tokens.cache_read_input_tokens,
        tool_calls=summary.tool_calls,
        tool_errors=sum(c["status"] == "error" for c in run.tool_calls),
        estimated_cost_usd=summary.cost_usd,
        judge_model=judge.model if judge else None,
        judge_verdict=judge.verdict.behavior if judge and judge.verdict else None,
        judge_error=judge.error if judge else None,
        judge_usage=summary.judge_tokens.model_dump() if summary.judge_tokens else None,
        judge_cost_usd=summary.judge_cost_usd,
        started_at=run.started_at,
        checks=[
            EvaluationCheckRecord(
                name=c.name,
                target=c.target,
                source=str(c.source),
                severity=str(c.severity),
                passed=c.passed,
                reason=c.reason,
                expected=_json(c.expected),
                actual=_json(c.actual),
            )
            for c in evaluated.checks
        ],
        trace=ExecutionTraceRecord(
            turns=_json([dataclasses.asdict(t) for t in run.turns]),
            messages=_json(run.messages),
            initial_state=_state(run.initial_state),
            final_state=_state(run.final_state),
            injected_faults=_json([dataclasses.asdict(f) for f in run.injected_faults]),
        ),
    )
    db.add(record)
    return record


def load_summaries(db: Session, test_run_id: int) -> list[VariantSummary]:
    results = db.scalars(
        select(EvaluationResultRecord)
        .where(EvaluationResultRecord.test_run_id == test_run_id)
        .options(
            selectinload(EvaluationResultRecord.checks),
            selectinload(EvaluationResultRecord.scenario),
        )
        .order_by(EvaluationResultRecord.id)
    )
    return [
        VariantSummary(
            variant_id=r.variant_id,
            tags=list(r.scenario.tags),
            has_faults=bool(r.scenario.definition.get("faults")),
            verdict=Verdict(r.verdict),
            checks=[
                CheckResult(
                    name=c.name,
                    severity=Severity(c.severity),
                    passed=c.passed,
                    reason=c.reason,
                    expected=c.expected,
                    actual=c.actual,
                    target=c.target,
                    source=CheckSource(c.source),
                )
                for c in r.checks
            ],
            latency_ms=r.latency_ms,
            tokens=TokenTotals(
                llm_calls=r.llm_calls,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cache_creation_input_tokens=r.cache_creation_input_tokens,
                cache_read_input_tokens=r.cache_read_input_tokens,
            ),
            tool_calls=r.tool_calls,
            cost_usd=r.estimated_cost_usd,
            judge_tokens=TokenTotals.model_validate(r.judge_usage) if r.judge_usage else None,
            judge_cost_usd=r.judge_cost_usd,
        )
        for r in results
    ]
