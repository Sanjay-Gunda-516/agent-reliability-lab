"""Aggregate metrics, computed only from stored evaluation results.

Every rate carries its numerator and denominator, and only counts the variants it
applies to (e.g. intent accuracy only where the scenario specifies an intent). Crashed
runs count against task success and the crash rate, and are left out of the quality
rates because there was nothing to grade.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, computed_field

from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict


class Rate(BaseModel):
    passed: int
    total: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rate(self) -> float | None:
        return round(self.passed / self.total, 4) if self.total else None


class LatencyStats(BaseModel):
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class TokenTotals(BaseModel):
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class RunMetrics(BaseModel):
    variants: int
    passed: int
    failed: int
    errored: int
    task_success: Rate
    policy_compliance: Rate
    intent_accuracy: Rate
    tool_selection_accuracy: Rate
    tool_argument_accuracy: Rate
    escalation_accuracy: Rate
    failure_recovery: Rate
    crash_rate: Rate
    judge_behavior: Rate
    judge_grounded: Rate
    forbidden_tool_violations: int
    missed_escalations: int
    unnecessary_escalations: int
    failed_checks: dict[str, int]
    task_success_by_tag: dict[str, Rate]
    latency: LatencyStats
    tokens: TokenTotals
    judge_tokens: TokenTotals
    tool_calls: int
    estimated_cost_usd: Decimal | None
    estimated_judge_cost_usd: Decimal | None


@dataclass(frozen=True)
class VariantSummary:
    """What metrics need from one evaluated variant; built from a run or from DB rows."""

    variant_id: str
    tags: list[str]
    has_faults: bool
    verdict: Verdict
    checks: list[CheckResult]
    latency_ms: float
    tokens: TokenTotals
    tool_calls: int
    cost_usd: Decimal | None
    judge_tokens: TokenTotals | None = None
    judge_cost_usd: Decimal | None = None


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (no interpolation between two observed latencies)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def _rate(checks: list[CheckResult]) -> Rate:
    return Rate(passed=sum(c.passed for c in checks), total=len(checks))


def _sum_tokens(totals: list[TokenTotals]) -> TokenTotals:
    return TokenTotals(
        llm_calls=sum(t.llm_calls for t in totals),
        input_tokens=sum(t.input_tokens for t in totals),
        output_tokens=sum(t.output_tokens for t in totals),
        cache_creation_input_tokens=sum(t.cache_creation_input_tokens for t in totals),
        cache_read_input_tokens=sum(t.cache_read_input_tokens for t in totals),
    )


def _sum_costs(costs: list[Decimal | None]) -> Decimal | None:
    if any(c is None for c in costs):
        return None
    return sum((c for c in costs if c is not None), Decimal(0))


def compute_metrics(variants: list[VariantSummary]) -> RunMetrics:
    graded = [v for v in variants if v.verdict != Verdict.ERROR]
    verdicts = Counter(v.verdict for v in variants)

    def deterministic(name: str) -> list[CheckResult]:
        return [
            c
            for v in graded
            for c in v.checks
            if c.name == name and c.source == CheckSource.DETERMINISTIC
        ]

    def judged(name: str) -> list[CheckResult]:
        return [
            c for v in graded for c in v.checks if c.name == name and c.source == CheckSource.JUDGE
        ]

    def no_critical_failures(v: VariantSummary) -> bool:
        return not any(
            not c.passed
            and c.severity == Severity.CRITICAL
            and c.source == CheckSource.DETERMINISTIC
            for c in v.checks
        )

    def escalation_correct(v: VariantSummary) -> bool:
        relevant = [c for c in v.checks if c.name in ("status.final", "escalation.reason")]
        return all(c.passed for c in relevant)

    status_failures = [c for c in deterministic("status.final") if not c.passed]
    by_tag: dict[str, list[VariantSummary]] = defaultdict(list)
    for v in variants:
        for tag in v.tags:
            by_tag[tag].append(v)
    faulted = [v for v in variants if v.has_faults]
    latencies = [v.latency_ms for v in variants]

    return RunMetrics(
        variants=len(variants),
        passed=verdicts[Verdict.PASS],
        failed=verdicts[Verdict.FAIL],
        errored=verdicts[Verdict.ERROR],
        task_success=Rate(passed=verdicts[Verdict.PASS], total=len(variants)),
        policy_compliance=Rate(
            passed=sum(no_critical_failures(v) for v in graded), total=len(graded)
        ),
        intent_accuracy=_rate(deterministic("intent")),
        tool_selection_accuracy=_rate(deterministic("tools.selection")),
        tool_argument_accuracy=_rate(deterministic("tools.arguments")),
        escalation_accuracy=Rate(
            passed=sum(escalation_correct(v) for v in graded), total=len(graded)
        ),
        failure_recovery=Rate(
            passed=sum(v.verdict == Verdict.PASS for v in faulted), total=len(faulted)
        ),
        crash_rate=Rate(passed=verdicts[Verdict.ERROR], total=len(variants)),
        judge_behavior=_rate(judged("judge.behavior")),
        judge_grounded=_rate(judged("judge.grounded")),
        forbidden_tool_violations=sum(not c.passed for c in deterministic("tools.forbidden")),
        missed_escalations=sum(c.expected == "escalated" for c in status_failures),
        unnecessary_escalations=sum(c.expected != "escalated" for c in status_failures),
        failed_checks=dict(
            sorted(
                Counter(
                    c.name
                    for v in variants
                    for c in v.checks
                    if not c.passed and c.source == CheckSource.DETERMINISTIC
                ).items()
            )
        ),
        task_success_by_tag={
            tag: Rate(passed=sum(v.verdict == Verdict.PASS for v in group), total=len(group))
            for tag, group in sorted(by_tag.items())
        },
        latency=LatencyStats(
            mean_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
            p50_ms=_percentile(latencies, 50),
            p95_ms=_percentile(latencies, 95),
            max_ms=max(latencies) if latencies else None,
        ),
        tokens=_sum_tokens([v.tokens for v in variants]),
        judge_tokens=_sum_tokens([v.judge_tokens for v in variants if v.judge_tokens]),
        tool_calls=sum(v.tool_calls for v in variants),
        estimated_cost_usd=_sum_costs([v.cost_usd for v in variants]),
        estimated_judge_cost_usd=_sum_costs(
            [v.judge_cost_usd for v in variants if v.judge_tokens is not None]
        ),
    )
