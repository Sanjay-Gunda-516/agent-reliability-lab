"""Comparing a candidate test run against a baseline: deterministic, no LLM involved.

Results are paired by variant id. A pair is only compared as an agent change when both
results were graded against the same scenario content (hash); otherwise the test itself
changed. Each variant gets a category, aggregate metrics are compared over the variants
the gate counts, and the gate applies fixed, configurable thresholds.

One run per variant is noisy, so a flip seen once is a `regression` (or `improvement`)
that a rerun can turn into `confirmed_regression` or `noisy`. Rerun evidence is passed in
here; this module never runs anything itself.
"""

from collections.abc import Callable, Iterable
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.metrics import Rate, RunMetrics, VariantSummary, compute_metrics
from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict


class Category(StrEnum):
    REGRESSION = "regression"  # worse in a single observation; rerun to confirm
    CONFIRMED_REGRESSION = "confirmed_regression"  # still worse in most reruns
    IMPROVEMENT = "improvement"
    UNCHANGED = "unchanged"
    NOISY = "noisy"  # reruns disagree with the first observation; not gated
    SCENARIO_CHANGED = "scenario_changed"  # the test changed, not (only) the agent
    MISSING_IN_CANDIDATE = "missing_in_candidate"
    NEW_IN_CANDIDATE = "new_in_candidate"


RERUNNABLE = frozenset({Category.REGRESSION, Category.IMPROVEMENT, Category.UNCHANGED})
REGRESSIONS = frozenset({Category.REGRESSION, Category.CONFIRMED_REGRESSION})
_VERDICT_RANK = {Verdict.ERROR: 0, Verdict.FAIL: 1, Verdict.PASS: 2}


class GateThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fail_on_new_critical: bool = True
    max_task_success_drop_pp: Decimal = Field(default=Decimal(5), ge=0, le=100)
    max_cost_increase_pct: Decimal = Field(default=Decimal(25), ge=0)
    max_latency_increase_pct: Decimal = Field(default=Decimal(25), ge=0)


class Observation(BaseModel):
    """One graded run of a variant, as used for rerun evidence."""

    test_run_id: int | None
    result_id: int | None
    verdict: Verdict
    critical_failures: list[str]
    scenario_hash: str
    counted: bool  # False when graded against different scenario content
    reproduces: bool | None = None  # does it repeat the change being checked?


class RerunEvidence(BaseModel):
    first_category: Category
    outcome: Category
    candidate: list[Observation]
    baseline: list[Observation]
    candidate_reproduced: int  # candidate reruns that repeat the change
    candidate_counted: int
    baseline_reproduced: int  # baseline reruns that show the same change (i.e. flaky)
    baseline_counted: int
    explanation: str


class VariantComparison(BaseModel):
    variant_id: str
    scenario_id: str
    category: Category
    gated: bool
    needs_rerun: bool
    baseline_result_id: int | None
    candidate_result_id: int | None
    baseline_verdict: Verdict | None
    candidate_verdict: Verdict | None
    baseline_scenario_hash: str | None
    candidate_scenario_hash: str | None
    new_critical_failures: list[str]
    resolved_critical_failures: list[str]
    new_failed_checks: list[str]
    fixed_checks: list[str]
    latency_delta_ms: float | None
    cost_delta_usd: Decimal | None
    explanation: str
    rerun_evidence: RerunEvidence | None = None


class MetricDelta(BaseModel):
    name: str
    unit: Literal["pp", "ms", "usd", "count"]  # rates are compared in percentage points
    better: Literal["higher", "lower"]
    baseline: float | None
    candidate: float | None
    delta: float | None
    change_pct: float | None  # relative change, for non-rate metrics
    baseline_detail: str | None = None  # e.g. "18/20" for rates
    candidate_detail: str | None = None


class GateReason(BaseModel):
    code: str
    level: Literal["fail", "warn"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class GateResult(BaseModel):
    status: Literal["pass", "warn", "fail"]
    reasons: list[GateReason]


class Comparison(BaseModel):
    variants: list[VariantComparison]
    counts: dict[str, int]
    metric_deltas: list[MetricDelta]
    baseline_metrics: RunMetrics
    candidate_metrics: RunMetrics
    gate: GateResult


def check_key(check: CheckResult) -> str:
    return f"{check.name}[{check.target}]" if check.target else check.name


def _failed(summary: VariantSummary, *, critical_only: bool = False) -> set[str]:
    return {
        check_key(c)
        for c in summary.checks
        if not c.passed
        and c.source == CheckSource.DETERMINISTIC
        and (not critical_only or c.severity == Severity.CRITICAL)
    }


def _worse(observed: VariantSummary, reference: VariantSummary) -> bool:
    return _VERDICT_RANK[observed.verdict] < _VERDICT_RANK[reference.verdict] or bool(
        _failed(observed, critical_only=True) - _failed(reference, critical_only=True)
    )


def _better(observed: VariantSummary, reference: VariantSummary) -> bool:
    if _worse(observed, reference):
        return False
    return _VERDICT_RANK[observed.verdict] > _VERDICT_RANK[reference.verdict] or bool(
        _failed(reference, critical_only=True) - _failed(observed, critical_only=True)
    )


def _differs(observed: VariantSummary, reference: VariantSummary) -> bool:
    return observed.verdict != reference.verdict or _failed(
        observed, critical_only=True
    ) != _failed(reference, critical_only=True)


def _observation(summary: VariantSummary, reference_hash: str) -> Observation:
    return Observation(
        test_run_id=summary.test_run_id,
        result_id=summary.result_id,
        verdict=summary.verdict,
        critical_failures=sorted(_failed(summary, critical_only=True)),
        scenario_hash=summary.scenario_hash,
        counted=summary.scenario_hash == reference_hash,
    )


def _first_category(baseline: VariantSummary, candidate: VariantSummary) -> Category:
    if _worse(candidate, baseline):
        return Category.REGRESSION
    if _better(candidate, baseline):
        return Category.IMPROVEMENT
    return Category.UNCHANGED


def _majority(count: int, total: int) -> bool:
    return 2 * count > total


def assess_reruns(
    baseline: VariantSummary,
    candidate: VariantSummary,
    *,
    candidate_reruns: Iterable[VariantSummary],
    baseline_reruns: Iterable[VariantSummary] = (),
) -> RerunEvidence:
    """Decide whether a first-run change holds up when the variant is repeated.

    A regression is confirmed when most candidate reruns are again worse than the
    original baseline result, and fewer than half of any baseline reruns are. If the
    baseline fails the same way just as often, the variant is flaky rather than the
    candidate being worse. Otherwise the change is noisy. Improvements are judged the
    same way, mirrored. A variant that was unchanged becomes noisy if any rerun differs.
    Reruns graded against different scenario content are recorded but not counted.
    """
    first = _first_category(baseline, candidate)
    changed: Callable[[VariantSummary, VariantSummary], bool] = {
        Category.REGRESSION: _worse,
        Category.IMPROVEMENT: _better,
        Category.UNCHANGED: _differs,
    }[first]
    # An unchanged variant is checked against its own first result on each side.
    cand_reference = candidate if first == Category.UNCHANGED else baseline

    def observe(runs: Iterable[VariantSummary], reference: VariantSummary) -> list[Observation]:
        observations = []
        for summary in runs:
            observation = _observation(summary, baseline.scenario_hash)
            if observation.counted:
                observation.reproduces = changed(summary, reference)
            observations.append(observation)
        return observations

    cand_obs = observe(candidate_reruns, cand_reference)
    base_obs = observe(baseline_reruns, baseline)
    n = sum(o.counted for o in cand_obs)
    r = sum(bool(o.reproduces) for o in cand_obs)
    m = sum(o.counted for o in base_obs)
    d = sum(bool(o.reproduces) for o in base_obs)

    if n == 0:
        outcome = first
        explanation = "No rerun was graded against the same scenario content, so nothing changed."
    elif first == Category.UNCHANGED:
        outcome = Category.NOISY if r or d else Category.UNCHANGED
        explanation = (
            f"Candidate reruns differed from its first result in {r}/{n}"
            + (f", baseline reruns in {d}/{m}" if m else "")
            + "."
        )
    else:
        what = "the regression" if first == Category.REGRESSION else "the improvement"
        baseline_stable = m == 0 or 2 * d < m
        confirmed = _majority(r, n) and baseline_stable
        if confirmed:
            outcome = Category.CONFIRMED_REGRESSION if first == Category.REGRESSION else first
        else:
            outcome = Category.NOISY
        explanation = f"Candidate reruns repeated {what} in {r}/{n}"
        if m:
            explanation += f"; baseline reruns showed the same change in {d}/{m}"
        if confirmed:
            explanation += ", so it holds up"
            if r < n or d:
                explanation += " (the variant is somewhat flaky)"
        elif not _majority(r, n):
            explanation += ", so the first result looks like noise"
        else:
            explanation += ", so the baseline is just as unstable and this looks like noise"
        explanation += "."
    return RerunEvidence(
        first_category=first,
        outcome=outcome,
        candidate=cand_obs,
        baseline=base_obs,
        candidate_reproduced=r,
        candidate_counted=n,
        baseline_reproduced=d,
        baseline_counted=m,
        explanation=explanation,
    )


def _describe(baseline: VariantSummary, candidate: VariantSummary, new_critical: list[str],
              resolved_critical: list[str]) -> str:  # fmt: skip
    parts = []
    if baseline.verdict != candidate.verdict:
        parts.append(f"{baseline.verdict} → {candidate.verdict}")
    if new_critical:
        parts.append("newly failing critical: " + ", ".join(new_critical))
    if resolved_critical:
        parts.append("critical fixed: " + ", ".join(resolved_critical))
    return "; ".join(parts) or f"{candidate.verdict} in both runs"


def _compare_variant(
    variant_id: str,
    baseline: VariantSummary | None,
    candidate: VariantSummary | None,
    evidence: RerunEvidence | None,
) -> VariantComparison:
    either = baseline or candidate
    assert either is not None
    fields: dict[str, Any] = {
        "variant_id": variant_id,
        "scenario_id": either.scenario_id,
        "baseline_result_id": baseline.result_id if baseline else None,
        "candidate_result_id": candidate.result_id if candidate else None,
        "baseline_verdict": baseline.verdict if baseline else None,
        "candidate_verdict": candidate.verdict if candidate else None,
        "baseline_scenario_hash": baseline.scenario_hash if baseline else None,
        "candidate_scenario_hash": candidate.scenario_hash if candidate else None,
        "new_critical_failures": [],
        "resolved_critical_failures": [],
        "new_failed_checks": [],
        "fixed_checks": [],
        "latency_delta_ms": None,
        "cost_delta_usd": None,
        "needs_rerun": False,
        "gated": False,
    }
    if candidate is None:
        return VariantComparison(
            **fields,
            category=Category.MISSING_IN_CANDIDATE,
            explanation="Only in the baseline run.",
        )
    if baseline is None:
        return VariantComparison(
            **fields,
            category=Category.NEW_IN_CANDIDATE,
            explanation="Only in the candidate run.",
        )
    fields["latency_delta_ms"] = round(candidate.latency_ms - baseline.latency_ms, 1)
    if baseline.cost_usd is not None and candidate.cost_usd is not None:
        fields["cost_delta_usd"] = candidate.cost_usd - baseline.cost_usd
    if baseline.scenario_hash != candidate.scenario_hash:
        return VariantComparison(
            **fields,
            category=Category.SCENARIO_CHANGED,
            explanation=(
                "The scenario's content changed between the runs "
                f"({baseline.scenario_hash[:8]} → {candidate.scenario_hash[:8]}), so the "
                "difference isn't attributed to the agent."
            ),
        )

    base_critical = _failed(baseline, critical_only=True)
    cand_critical = _failed(candidate, critical_only=True)
    new_critical = sorted(cand_critical - base_critical)
    resolved_critical = sorted(base_critical - cand_critical)
    category = _first_category(baseline, candidate)
    explanation = _describe(baseline, candidate, new_critical, resolved_critical)
    if evidence is not None:
        category = evidence.outcome
        explanation = f"{explanation}. {evidence.explanation}"
    return VariantComparison(
        **{
            **fields,
            "new_critical_failures": new_critical,
            "resolved_critical_failures": resolved_critical,
            "new_failed_checks": sorted(_failed(candidate) - _failed(baseline)),
            "fixed_checks": sorted(_failed(baseline) - _failed(candidate)),
            "needs_rerun": category == Category.REGRESSION,
            "gated": category != Category.NOISY,
        },
        category=category,
        explanation=explanation,
        rerun_evidence=evidence,
    )


def _fraction(rate: Rate) -> Fraction | None:
    return Fraction(rate.passed, rate.total) if rate.total else None


def _pp(value: Fraction | None) -> float | None:
    return None if value is None else round(float(value * 100), 2)


def _rate_delta(
    name: str, base: Rate, cand: Rate, better: Literal["higher", "lower"]
) -> MetricDelta:
    b, c = _fraction(base), _fraction(cand)
    return MetricDelta(
        name=name,
        unit="pp",
        better=better,
        baseline=_pp(b),
        candidate=_pp(c),
        delta=_pp(c - b) if b is not None and c is not None else None,
        change_pct=None,
        baseline_detail=f"{base.passed}/{base.total}",
        candidate_detail=f"{cand.passed}/{cand.total}",
    )


def _change_pct(base: Fraction, cand: Fraction) -> Fraction | None:
    return (cand - base) / base * 100 if base else None


def _value_delta(
    name: str,
    base: float | Decimal | None,
    cand: float | Decimal | None,
    unit: Literal["ms", "usd", "count"],
) -> MetricDelta:
    if base is None or cand is None:
        delta = change = None
    else:
        delta = float(Fraction(cand) - Fraction(base))
        pct = _change_pct(Fraction(base), Fraction(cand))
        change = round(float(pct), 2) if pct is not None else None
    return MetricDelta(
        name=name,
        unit=unit,
        better="lower",
        baseline=None if base is None else float(base),
        candidate=None if cand is None else float(cand),
        delta=None if delta is None else round(delta, 6),
        change_pct=change,
    )


RATE_METRICS: list[tuple[str, Literal["higher", "lower"]]] = [
    ("task_success", "higher"),
    ("policy_compliance", "higher"),
    ("intent_accuracy", "higher"),
    ("tool_selection_accuracy", "higher"),
    ("tool_argument_accuracy", "higher"),
    ("escalation_accuracy", "higher"),
    ("failure_recovery", "higher"),
    ("crash_rate", "lower"),
]


def metric_deltas(base: RunMetrics, cand: RunMetrics) -> list[MetricDelta]:
    deltas = [_rate_delta(name, getattr(base, name), getattr(cand, name), better)
              for name, better in RATE_METRICS]  # fmt: skip
    for name, b, c, unit in [
        ("latency_mean_ms", base.latency.mean_ms, cand.latency.mean_ms, "ms"),
        ("latency_p95_ms", base.latency.p95_ms, cand.latency.p95_ms, "ms"),
        ("estimated_cost_usd", base.estimated_cost_usd, cand.estimated_cost_usd, "usd"),
        ("llm_calls", base.tokens.llm_calls, cand.tokens.llm_calls, "count"),
        ("input_tokens", base.tokens.input_tokens, cand.tokens.input_tokens, "count"),
        ("output_tokens", base.tokens.output_tokens, cand.tokens.output_tokens, "count"),
        ("cache_creation_input_tokens", base.tokens.cache_creation_input_tokens,
         cand.tokens.cache_creation_input_tokens, "count"),
        ("cache_read_input_tokens", base.tokens.cache_read_input_tokens,
         cand.tokens.cache_read_input_tokens, "count"),
        ("tool_calls", base.tool_calls, cand.tool_calls, "count"),
        ("forbidden_tool_violations", base.forbidden_tool_violations,
         cand.forbidden_tool_violations, "count"),
        ("missed_escalations", base.missed_escalations, cand.missed_escalations, "count"),
        ("unnecessary_escalations", base.unnecessary_escalations,
         cand.unnecessary_escalations, "count"),
    ]:  # fmt: skip
        deltas.append(_value_delta(name, b, c, unit))  # type: ignore[arg-type]
    return deltas


def _ids(variants: Iterable[VariantComparison]) -> list[str]:
    return [v.variant_id for v in variants]


def evaluate_gate(
    variants: list[VariantComparison],
    gated_baseline: list[VariantSummary],
    gated_candidate: list[VariantSummary],
    thresholds: GateThresholds,
) -> GateResult:
    reasons: list[GateReason] = []
    gated = [v for v in variants if v.gated and v.category in _PAIRED]
    by_category = {c: [v for v in variants if v.category == c] for c in Category}

    if not gated:
        reasons.append(GateReason(
            code="nothing_to_gate", level="fail",
            message="No variant could be compared, so the candidate can't be judged.",
        ))  # fmt: skip

    critical = {v.variant_id: v.new_critical_failures for v in gated if v.new_critical_failures}
    if critical:
        reasons.append(GateReason(
            code="new_critical_failures",
            level="fail" if thresholds.fail_on_new_critical else "warn",
            message=f"{len(critical)} variant(s) newly fail a critical check.",
            details={"variants": critical},
        ))  # fmt: skip

    if gated:
        base = compute_metrics(gated_baseline).task_success
        cand = compute_metrics(gated_candidate).task_success
        b, c = _fraction(base), _fraction(cand)
        assert b is not None and c is not None
        drop = (b - c) * 100
        limit = Fraction(thresholds.max_task_success_drop_pp)
        if drop > limit:
            reasons.append(GateReason(
                code="task_success_drop", level="fail",
                message=(f"Task success fell {float(drop):.2f} points "
                         f"({base.passed}/{base.total} → {cand.passed}/{cand.total}), "
                         f"more than the {thresholds.max_task_success_drop_pp}-point limit."),
                details={"baseline": _pp(b), "candidate": _pp(c), "drop_pp": _pp(drop / 100),
                         "limit_pp": float(limit)},
            ))  # fmt: skip

        reasons.extend(_cost_and_latency(gated_baseline, gated_candidate, thresholds))

    plain = [v for v in gated if v.category in REGRESSIONS and not v.new_critical_failures]
    if plain:
        reasons.append(GateReason(
            code="variant_regressions", level="warn",
            message=f"{len(plain)} variant(s) got worse without failing a new critical check.",
            details={"variants": _ids(plain)},
        ))  # fmt: skip
    unconfirmed = by_category[Category.REGRESSION]
    if unconfirmed:
        reasons.append(GateReason(
            code="rerun_recommended", level="warn",
            message=(f"{len(unconfirmed)} regression(s) were seen in a single run; rerun them "
                     "to tell a real regression from model noise."),
            details={"variants": _ids(unconfirmed)},
        ))  # fmt: skip
    for category, code, message in [
        (Category.NOISY, "noisy_variants", "variant(s) gave inconsistent results on rerun and "
         "are left out of the gate"),
        (Category.SCENARIO_CHANGED, "scenario_changed", "variant(s) were left out because "
         "their scenario changed between the runs"),
        (Category.MISSING_IN_CANDIDATE, "missing_in_candidate", "baseline variant(s) were not "
         "run by the candidate"),
    ]:  # fmt: skip
        if by_category[category]:
            reasons.append(GateReason(
                code=code, level="warn",
                message=f"{len(by_category[category])} {message}.",
                details={"variants": _ids(by_category[category])},
            ))  # fmt: skip

    levels = {r.level for r in reasons}
    status: Literal["pass", "warn", "fail"] = (
        "fail" if "fail" in levels else "warn" if "warn" in levels else "pass"
    )
    return GateResult(status=status, reasons=reasons)


def _cost_and_latency(
    baseline: list[VariantSummary], candidate: list[VariantSummary], thresholds: GateThresholds
) -> list[GateReason]:
    reasons = []
    base_costs = [v.cost_usd for v in baseline]
    cand_costs = [v.cost_usd for v in candidate]
    if any(c is None for c in base_costs + cand_costs):
        reasons.append(GateReason(
            code="cost_unknown", level="warn",
            message="Cost couldn't be estimated for some results (unknown model price).",
        ))  # fmt: skip
    else:
        reasons.extend(_increase(
            "cost_increase", "Estimated cost",
            sum((Fraction(c) for c in base_costs if c is not None), Fraction(0)),
            sum((Fraction(c) for c in cand_costs if c is not None), Fraction(0)),
            thresholds.max_cost_increase_pct,
        ))  # fmt: skip
    reasons.extend(_increase(
        "latency_increase", "Mean latency",
        Fraction(sum(Fraction(v.latency_ms) for v in baseline), len(baseline)),
        Fraction(sum(Fraction(v.latency_ms) for v in candidate), len(candidate)),
        thresholds.max_latency_increase_pct,
    ))  # fmt: skip
    return reasons


def _increase(
    code: str, label: str, base: Fraction, cand: Fraction, limit_pct: Decimal
) -> list[GateReason]:
    if base == 0:
        if cand == 0:
            return []
        return [GateReason(code=code, level="warn",
                           message=f"{label} went up from zero.",
                           details={"baseline": 0.0, "candidate": float(cand)})]  # fmt: skip
    pct = _change_pct(base, cand)
    assert pct is not None
    if pct <= Fraction(limit_pct):
        return []
    return [GateReason(
        code=code, level="warn",
        message=f"{label} rose {float(pct):.1f}%, more than the {limit_pct}% limit.",
        details={"baseline": float(base), "candidate": float(cand),
                 "change_pct": round(float(pct), 2), "limit_pct": float(limit_pct)},
    )]  # fmt: skip


_PAIRED = frozenset(
    {Category.REGRESSION, Category.CONFIRMED_REGRESSION, Category.IMPROVEMENT, Category.UNCHANGED}
)


def compare(
    baseline: list[VariantSummary],
    candidate: list[VariantSummary],
    thresholds: GateThresholds | None = None,
    evidence: dict[str, RerunEvidence] | None = None,
) -> Comparison:
    thresholds = thresholds or GateThresholds()
    evidence = evidence or {}
    base_by_id = {v.variant_id: v for v in baseline}
    cand_by_id = {v.variant_id: v for v in candidate}
    variants = [
        _compare_variant(vid, base_by_id.get(vid), cand_by_id.get(vid), evidence.get(vid))
        for vid in sorted(base_by_id.keys() | cand_by_id.keys())
    ]
    gated_ids = [v.variant_id for v in variants if v.gated and v.category in _PAIRED]
    gated_base = [base_by_id[i] for i in gated_ids]
    gated_cand = [cand_by_id[i] for i in gated_ids]
    base_metrics, cand_metrics = compute_metrics(gated_base), compute_metrics(gated_cand)
    return Comparison(
        variants=variants,
        counts={str(c): sum(v.category == c for v in variants) for c in Category},
        metric_deltas=metric_deltas(base_metrics, cand_metrics),
        baseline_metrics=base_metrics,
        candidate_metrics=cand_metrics,
        gate=evaluate_gate(variants, gated_base, gated_cand, thresholds),
    )
