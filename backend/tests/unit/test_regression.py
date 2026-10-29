"""The deterministic comparison: categories, metric deltas, the gate and rerun evidence."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.evaluation.metrics import TokenTotals, VariantSummary
from app.evaluation.regression import (
    Category,
    GateThresholds,
    assess_reruns,
    compare,
)
from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict

TOKENS = TokenTotals(
    llm_calls=2,
    input_tokens=1000,
    output_tokens=100,
    cache_creation_input_tokens=0,
    cache_read_input_tokens=0,
)
FORBIDDEN = CheckResult("tools.forbidden", Severity.CRITICAL, False, "r", target="cancel_order")
WRONG_ARGS = CheckResult("tools.arguments", Severity.MAJOR, False, "r", target="cancel_order")
CRASH = CheckResult("execution.crash", Severity.CRITICAL, False, "r")


def v(
    variant_id: str = "s:normal",
    verdict: Verdict = Verdict.PASS,
    checks: list[CheckResult] | None = None,
    *,
    latency_ms: float = 1000.0,
    cost: Decimal | None = Decimal("0.010"),
    scenario_hash: str = "h1",
    checks_version: str | None = "checks-v2",
    tags: list[str] | None = None,
    has_faults: bool = False,
    run: int = 1,
) -> VariantSummary:
    return VariantSummary(
        variant_id=variant_id,
        tags=tags or ["happy-path"],
        has_faults=has_faults,
        verdict=verdict,
        checks=checks or [],
        latency_ms=latency_ms,
        tokens=TOKENS,
        tool_calls=1,
        cost_usd=cost,
        scenario_id=variant_id.split(":")[0],
        scenario_hash=scenario_hash,
        check_set_version=checks_version,
        result_id=hash((variant_id, run)) % 10_000,
        test_run_id=run,
    )


def passing(n: int, **kwargs: object) -> list[VariantSummary]:
    return [v(f"s{i}:normal", **kwargs) for i in range(n)]  # type: ignore[arg-type]


def fail(variant_id: str = "s:normal", *checks: CheckResult, **kwargs: object) -> VariantSummary:
    return v(variant_id, Verdict.FAIL, list(checks or [WRONG_ARGS]), **kwargs)  # type: ignore[arg-type]


def category(comparison: object, variant_id: str = "s:normal") -> Category:
    return next(x.category for x in comparison.variants if x.variant_id == variant_id)  # type: ignore[attr-defined]


def codes(comparison: object) -> dict[str, str]:
    return {r.code: r.level for r in comparison.gate.reasons}  # type: ignore[attr-defined]


class TestVariantCategories:
    def test_pass_to_fail_is_a_regression_that_needs_a_rerun(self) -> None:
        result = compare([v()], [fail()])

        [variant] = result.variants
        assert variant.category == Category.REGRESSION and variant.needs_rerun
        assert variant.new_failed_checks == ["tools.arguments[cancel_order]"]
        assert variant.new_critical_failures == []
        assert "pass → fail" in variant.explanation

    def test_fail_to_pass_is_an_improvement(self) -> None:
        result = compare([fail()], [v()])

        [variant] = result.variants
        assert variant.category == Category.IMPROVEMENT and not variant.needs_rerun
        assert variant.fixed_checks == ["tools.arguments[cancel_order]"]

    def test_still_failing_with_a_new_critical_check_is_a_regression(self) -> None:
        result = compare([fail()], [fail("s:normal", WRONG_ARGS, FORBIDDEN)])

        [variant] = result.variants
        assert variant.category == Category.REGRESSION
        assert variant.new_critical_failures == ["tools.forbidden[cancel_order]"]

    def test_fixing_a_critical_failure_while_still_failing_is_an_improvement(self) -> None:
        result = compare([fail("s:normal", FORBIDDEN, WRONG_ARGS)], [fail()])

        [variant] = result.variants
        assert variant.category == Category.IMPROVEMENT
        assert variant.resolved_critical_failures == ["tools.forbidden[cancel_order]"]

    def test_crash_counts_as_a_new_critical_failure(self) -> None:
        result = compare([v()], [v(verdict=Verdict.ERROR, checks=[CRASH])])

        assert result.variants[0].new_critical_failures == ["execution.crash"]
        assert result.gate.status == "fail"

    def test_same_outcome_is_unchanged(self) -> None:
        result = compare([fail(), v("t:normal")], [fail(), v("t:normal")])

        assert {x.category for x in result.variants} == {Category.UNCHANGED}

    def test_changed_scenario_content_is_not_blamed_on_the_agent(self) -> None:
        result = compare(
            [v(), v("t:normal")],
            [fail(scenario_hash="h2"), v("t:normal")],
        )

        changed = result.variants[0]
        assert changed.category == Category.SCENARIO_CHANGED and not changed.gated
        assert "h1" in changed.explanation and "h2" in changed.explanation
        # Excluded from the metrics too: the only gated variant passes on both sides.
        assert result.baseline_metrics.variants == result.candidate_metrics.variants == 1
        assert codes(result) == {"scenario_changed": "warn"}

    def test_different_check_sets_are_not_compared(self) -> None:
        result = compare(
            [v(checks_version="checks-v1"), v("t:normal")],
            [fail(), v("t:normal")],
        )

        changed = result.variants[0]
        assert changed.category == Category.CHECKS_CHANGED and not changed.gated
        assert "checks-v1 → checks-v2" in changed.explanation
        assert codes(result) == {"checks_changed": "warn"}
        assert result.baseline_check_set_versions == ["checks-v1", "checks-v2"]

    def test_unknown_check_set_is_never_compared(self) -> None:
        result = compare([v(checks_version=None)], [v(checks_version=None)])

        assert result.variants[0].category == Category.CHECKS_CHANGED
        assert "unknown → unknown" in result.variants[0].explanation
        assert result.gate.status == "fail"  # nothing left to gate
        assert result.candidate_check_set_versions == [None]

    def test_variants_on_one_side_only(self) -> None:
        result = compare([v("a:normal"), v("b:normal")], [v("b:normal"), v("c:normal")])

        assert category(result, "a:normal") == Category.MISSING_IN_CANDIDATE
        assert category(result, "c:normal") == Category.NEW_IN_CANDIDATE
        assert codes(result) == {"missing_in_candidate": "warn"}
        assert result.counts["unchanged"] == 1


class TestGate:
    def test_identical_runs_pass(self) -> None:
        result = compare(passing(10), passing(10))

        assert result.gate.status == "pass" and result.gate.reasons == []

    def test_new_critical_failure_fails_the_gate(self) -> None:
        base = passing(20)
        cand = passing(19) + [fail("s19:normal", FORBIDDEN)]

        result = compare(base, cand)  # a 5-point drop alone wouldn't fail

        assert result.gate.status == "fail"
        reason = next(r for r in result.gate.reasons if r.code == "new_critical_failures")
        assert reason.details == {"variants": {"s19:normal": ["tools.forbidden[cancel_order]"]}}
        assert "task_success_drop" not in codes(result)

    def test_critical_rule_can_be_turned_into_a_warning(self) -> None:
        cand = passing(19) + [fail("s19:normal", FORBIDDEN)]

        result = compare(passing(20), cand, GateThresholds(fail_on_new_critical=False))

        assert codes(result)["new_critical_failures"] == "warn"
        assert result.gate.status == "warn"

    def test_drop_of_exactly_the_threshold_passes(self) -> None:
        # 20/20 -> 19/20 is exactly 5.00 points.
        result = compare(passing(20), passing(19) + [fail("s19:normal")])

        assert "task_success_drop" not in codes(result)
        assert result.gate.status == "warn"  # the regression itself is still reported
        assert set(codes(result)) == {"variant_regressions", "rerun_recommended"}
        task = next(d for d in result.metric_deltas if d.name == "task_success")
        assert (task.baseline, task.candidate, task.delta) == (100.0, 95.0, -5.0)
        assert (task.baseline_detail, task.candidate_detail) == ("20/20", "19/20")

    def test_drop_over_the_threshold_fails(self) -> None:
        # 20/20 -> 18/20 is 10 points.
        cand = passing(18) + [fail("s18:normal"), fail("s19:normal")]

        result = compare(passing(20), cand)

        assert codes(result)["task_success_drop"] == "fail"
        reason = next(r for r in result.gate.reasons if r.code == "task_success_drop")
        assert reason.details["drop_pp"] == 10.0 and reason.details["limit_pp"] == 5.0

    def test_threshold_is_exact_for_awkward_fractions(self) -> None:
        # 3/3 -> 2/3 is 33.33... points: fails at 33.33, passes at 33.34.
        base, cand = passing(3), passing(2) + [fail("s2:normal")]

        tight = compare(base, cand, GateThresholds(max_task_success_drop_pp=Decimal("33.33")))
        loose = compare(base, cand, GateThresholds(max_task_success_drop_pp=Decimal("33.34")))

        assert "task_success_drop" in codes(tight)
        assert "task_success_drop" not in codes(loose)

    def test_custom_drop_threshold(self) -> None:
        cand = passing(18) + [fail("s18:normal"), fail("s19:normal")]

        result = compare(passing(20), cand, GateThresholds(max_task_success_drop_pp=Decimal(10)))

        assert "task_success_drop" not in codes(result)

    def test_cost_and_latency_increases_only_warn(self) -> None:
        base = passing(4, cost=Decimal("0.010"), latency_ms=1000.0)
        cand = passing(4, cost=Decimal("0.013"), latency_ms=1300.0)

        result = compare(base, cand)

        assert codes(result) == {"cost_increase": "warn", "latency_increase": "warn"}
        assert result.gate.status == "warn"
        cost = next(d for d in result.metric_deltas if d.name == "estimated_cost_usd")
        assert cost.change_pct == 30.0 and cost.delta == pytest.approx(0.012)

    def test_increase_of_exactly_the_threshold_does_not_warn(self) -> None:
        base = passing(4, cost=Decimal("0.0100"), latency_ms=1000.0)
        cand = passing(4, cost=Decimal("0.0125"), latency_ms=1250.0)

        assert compare(base, cand).gate.status == "pass"

    def test_custom_cost_and_latency_thresholds(self) -> None:
        base = passing(2, latency_ms=1000.0)
        cand = passing(2, cost=Decimal("0.0105"), latency_ms=1060.0)
        tight = GateThresholds(
            max_cost_increase_pct=Decimal(1), max_latency_increase_pct=Decimal(5)
        )

        assert codes(compare(base, cand, tight)) == {
            "cost_increase": "warn",
            "latency_increase": "warn",
        }

    def test_unknown_cost_warns_instead_of_guessing(self) -> None:
        result = compare(passing(2), passing(2, cost=None))

        assert codes(result) == {"cost_unknown": "warn"}

    def test_nothing_comparable_fails(self) -> None:
        result = compare([v()], [v(scenario_hash="h2")])

        assert codes(result) == {"nothing_to_gate": "fail", "scenario_changed": "warn"}

    def test_thresholds_are_validated(self) -> None:
        with pytest.raises(ValidationError):
            GateThresholds(max_task_success_drop_pp=Decimal(-1))
        with pytest.raises(ValidationError):
            GateThresholds.model_validate({"max_pass_drop": 3})

    def test_policy_and_tool_metric_deltas(self) -> None:
        selection = CheckResult("tools.selection", Severity.MAJOR, True, "r", target="get_order")
        missed = CheckResult("tools.selection", Severity.MAJOR, False, "r", target="get_order")
        base = [v(checks=[selection]), v("t:normal", checks=[selection])]
        cand = [fail("s:normal", missed, FORBIDDEN), v("t:normal", checks=[selection])]

        deltas = {d.name: d for d in compare(base, cand).metric_deltas}

        assert deltas["policy_compliance"].delta == -50.0
        assert deltas["tool_selection_accuracy"].delta == -50.0
        assert deltas["forbidden_tool_violations"].delta == 1.0
        assert deltas["crash_rate"].better == "lower"

    def test_failure_recovery_and_escalation_deltas(self) -> None:
        status = CheckResult("status.final", Severity.CRITICAL, False, "r", expected="escalated")
        base = [v(has_faults=True), v("t:normal")]
        cand = [fail("s:normal", has_faults=True), fail("t:normal", status)]

        deltas = {d.name: d for d in compare(base, cand).metric_deltas}

        assert deltas["failure_recovery"].delta == -100.0
        assert deltas["escalation_accuracy"].delta == -50.0
        assert deltas["missed_escalations"].delta == 1.0

    def test_judge_findings_never_affect_the_gate(self) -> None:
        judged = CheckResult("judge.behavior", Severity.MAJOR, False, "r", source=CheckSource.JUDGE)

        assert compare([v()], [v(checks=[judged])]).gate.status == "pass"


class TestReruns:
    def test_regression_reproduced_in_every_rerun_is_confirmed(self) -> None:
        evidence = assess_reruns(
            v(), fail(), candidate_reruns=[fail(run=2), fail(run=3), fail(run=4)]
        )

        assert evidence.outcome == Category.CONFIRMED_REGRESSION
        assert (evidence.candidate_reproduced, evidence.candidate_counted) == (3, 3)
        assert "3/3" in evidence.explanation

    def test_majority_with_a_stable_baseline_is_confirmed_but_flagged_flaky(self) -> None:
        evidence = assess_reruns(
            v(),
            fail(),
            candidate_reruns=[fail(run=2), fail(run=3), v(run=4)],
            baseline_reruns=[v(run=5), v(run=6), v(run=7)],
        )

        assert evidence.outcome == Category.CONFIRMED_REGRESSION
        assert "flaky" in evidence.explanation

    def test_regression_that_does_not_reproduce_is_noise(self) -> None:
        evidence = assess_reruns(v(), fail(), candidate_reruns=[v(run=2), v(run=3), fail(run=4)])

        assert evidence.outcome == Category.NOISY
        assert "1/3" in evidence.explanation and "noise" in evidence.explanation

    def test_baseline_failing_the_same_way_makes_it_noise(self) -> None:
        evidence = assess_reruns(
            v(),
            fail(),
            candidate_reruns=[fail(run=2), fail(run=3), fail(run=4)],
            baseline_reruns=[fail(run=5), fail(run=6), v(run=7)],
        )

        assert evidence.outcome == Category.NOISY
        assert (evidence.baseline_reproduced, evidence.baseline_counted) == (2, 3)

    def test_improvement_that_holds_stays_an_improvement(self) -> None:
        evidence = assess_reruns(fail(), v(), candidate_reruns=[v(run=2), v(run=3)])

        assert evidence.outcome == Category.IMPROVEMENT

    def test_improvement_that_does_not_hold_is_noise(self) -> None:
        evidence = assess_reruns(fail(), v(), candidate_reruns=[fail(run=2), fail(run=3)])

        assert evidence.outcome == Category.NOISY

    def test_reruns_against_changed_scenario_content_are_not_counted(self) -> None:
        evidence = assess_reruns(v(), fail(), candidate_reruns=[fail(run=2, scenario_hash="h2")])

        assert evidence.outcome == Category.REGRESSION
        assert evidence.candidate[0].counted is False and evidence.candidate_counted == 0

    def test_reruns_graded_by_other_checks_are_not_counted(self) -> None:
        evidence = assess_reruns(
            v(), fail(), candidate_reruns=[fail(run=2, checks_version="checks-v3")]
        )

        assert evidence.outcome == Category.REGRESSION and evidence.candidate_counted == 0
        assert evidence.candidate[0].check_set_version == "checks-v3"

    def test_evidence_reclassifies_and_noise_leaves_the_gate(self) -> None:
        base = passing(10)
        cand = passing(8) + [fail("s8:normal"), fail("s9:normal", FORBIDDEN)]
        first = compare(base, cand)
        assert first.gate.status == "fail"
        assert set(codes(first)) >= {"new_critical_failures", "task_success_drop"}

        noise = assess_reruns(base[9], cand[9], candidate_reruns=[v("s9:normal", run=2)] * 3)
        real = assess_reruns(base[8], cand[8], candidate_reruns=[fail("s8:normal", run=2)] * 3)
        revised = compare(base, cand, evidence={"s9:normal": noise, "s8:normal": real})

        assert category(revised, "s9:normal") == Category.NOISY
        assert category(revised, "s8:normal") == Category.CONFIRMED_REGRESSION
        noisy = next(x for x in revised.variants if x.variant_id == "s9:normal")
        assert not noisy.gated and noisy.rerun_evidence is not None
        # The noisy critical failure no longer fails the gate; 10/10 -> 8/9 is an 11-point
        # drop on the remaining variants, which still does.
        assert "new_critical_failures" not in codes(revised)
        assert codes(revised)["task_success_drop"] == "fail"
        assert codes(revised)["noisy_variants"] == "warn"
        assert "rerun_recommended" not in codes(revised)

    def test_comparison_is_deterministic(self) -> None:
        base = passing(5) + [fail("x:normal")]
        cand = [fail("s0:normal", FORBIDDEN)] + passing(5)[1:] + [v("x:normal")]

        assert compare(base, cand).model_dump() == compare(list(reversed(base)), cand).model_dump()
