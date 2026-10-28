"""Grade one scenario run: deterministic checks, the optional judge, and a summary."""

import logging
from dataclasses import dataclass

from app.evaluation.checks import evaluate_run
from app.evaluation.judge import Judge, JudgeResult, build_judge_request, judge_checks
from app.evaluation.metrics import TokenTotals, VariantSummary
from app.evaluation.pricing import cost_of_calls, estimate_cost
from app.evaluation.results import CheckResult, Verdict, verdict_for
from app.scenarios.runner import ScenarioRun
from app.scenarios.schema import Scenario

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluatedVariant:
    scenario: Scenario
    run: ScenarioRun
    checks: list[CheckResult]
    verdict: Verdict
    judge_result: JudgeResult | None
    summary: VariantSummary


def _judge(judge: Judge, scenario: Scenario, run: ScenarioRun) -> JudgeResult:
    try:
        return judge.judge(build_judge_request(scenario, run))
    except Exception as exc:
        # The judge is a second opinion; it must never take down an evaluation.
        logger.exception("Judge crashed on %s", run.variant_id)
        return JudgeResult(None, judge.model, error=f"{type(exc).__name__}: {exc}")


def evaluate_variant(scenario: Scenario, run: ScenarioRun, judge: Judge | None) -> EvaluatedVariant:
    checks = evaluate_run(scenario, run)
    judge_result = None
    if judge is not None and run.error is None and run.turns:
        judge_result = _judge(judge, scenario, run)
        checks += judge_checks(judge_result)

    calls = run.llm_calls
    judge_tokens = judge_cost = None
    if judge_result is not None:
        usage = judge_result.usage
        judge_tokens = TokenTotals(
            llm_calls=1,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
        )
        judge_cost = estimate_cost(
            judge_result.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_creation_input_tokens,
            usage.cache_read_input_tokens,
        )

    verdict = verdict_for(checks)
    summary = VariantSummary(
        variant_id=run.variant_id,
        tags=[str(tag) for tag in scenario.tags],
        has_faults=bool(scenario.faults),
        verdict=verdict,
        checks=checks,
        latency_ms=run.latency_ms,
        tokens=TokenTotals(
            llm_calls=len(calls),
            input_tokens=sum(c["input_tokens"] for c in calls),
            output_tokens=sum(c["output_tokens"] for c in calls),
            cache_creation_input_tokens=sum(c["cache_creation_input_tokens"] for c in calls),
            cache_read_input_tokens=sum(c["cache_read_input_tokens"] for c in calls),
        ),
        tool_calls=len(run.tool_calls),
        cost_usd=cost_of_calls(calls),
        judge_tokens=judge_tokens,
        judge_cost_usd=judge_cost,
    )
    return EvaluatedVariant(scenario, run, checks, verdict, judge_result, summary)
