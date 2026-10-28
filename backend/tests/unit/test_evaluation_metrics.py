from decimal import Decimal

from app.evaluation.metrics import TokenTotals, VariantSummary, compute_metrics
from app.evaluation.pricing import cost_of_calls, estimate_cost
from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict
from tests.evaluation_helpers import llm_call

TOKENS = TokenTotals(
    llm_calls=2,
    input_tokens=1000,
    output_tokens=100,
    cache_creation_input_tokens=0,
    cache_read_input_tokens=0,
)


def check(
    name: str,
    passed: bool = True,
    severity: Severity = Severity.MAJOR,
    expected: object = None,
    source: CheckSource = CheckSource.DETERMINISTIC,
) -> CheckResult:
    return CheckResult(name, severity, passed, "r", expected=expected, source=source)


def variant(
    verdict: Verdict = Verdict.PASS,
    checks: list[CheckResult] | None = None,
    tags: list[str] | None = None,
    has_faults: bool = False,
    latency_ms: float = 1000.0,
    cost: Decimal | None = Decimal("0.01"),
) -> VariantSummary:
    return VariantSummary(
        variant_id="s:normal",
        tags=tags or ["happy-path"],
        has_faults=has_faults,
        verdict=verdict,
        checks=checks or [],
        latency_ms=latency_ms,
        tokens=TOKENS,
        tool_calls=1,
        cost_usd=cost,
    )


class TestPricing:
    def test_opus_prices_including_cache(self) -> None:
        cost = estimate_cost("claude-opus-5", 1_000_000, 100_000, 200_000, 1_000_000)

        # 5 + 0.2M * 6.25/M + 1M * 0.5/M + 0.1M * 25/M
        assert cost == Decimal("5") + Decimal("1.25") + Decimal("0.5") + Decimal("2.5")

    def test_unknown_model_is_not_guessed(self) -> None:
        assert estimate_cost("fake-claude", 100, 10) is None
        assert estimate_cost("fake-claude", 0, 0) == 0  # nothing was spent

    def test_cost_of_calls(self) -> None:
        calls = [llm_call(input_tokens=1000, output_tokens=100)] * 2

        assert cost_of_calls(calls) == Decimal("0.015")


class TestRates:
    def test_empty_run_has_no_rates(self) -> None:
        metrics = compute_metrics([])

        assert metrics.variants == 0
        assert metrics.task_success.rate is None
        assert metrics.latency.mean_ms is None

    def test_task_success_counts_errors_as_failures_but_quality_rates_skip_them(self) -> None:
        metrics = compute_metrics(
            [
                variant(Verdict.PASS, [check("intent", True, Severity.MINOR)]),
                variant(Verdict.FAIL, [check("intent", False, Severity.MINOR)]),
                variant(Verdict.ERROR, [check("execution.crash", False, Severity.CRITICAL)]),
            ]
        )

        assert (metrics.passed, metrics.failed, metrics.errored) == (1, 1, 1)
        assert metrics.task_success.model_dump() == {"passed": 1, "total": 3, "rate": 0.3333}
        assert metrics.crash_rate.passed == 1
        assert (metrics.intent_accuracy.passed, metrics.intent_accuracy.total) == (1, 2)
        assert metrics.policy_compliance.total == 2

    def test_policy_compliance_only_counts_critical_deterministic_failures(self) -> None:
        metrics = compute_metrics(
            [
                variant(Verdict.FAIL, [check("tools.selection", False, Severity.MAJOR)]),
                variant(Verdict.FAIL, [check("tools.forbidden", False, Severity.CRITICAL)]),
                variant(
                    Verdict.PASS,
                    [check("judge.grounded", False, Severity.CRITICAL, source=CheckSource.JUDGE)],
                ),
            ]
        )

        assert (metrics.policy_compliance.passed, metrics.policy_compliance.total) == (2, 3)
        assert metrics.forbidden_tool_violations == 1

    def test_tool_accuracy_uses_only_the_checks_that_ran(self) -> None:
        metrics = compute_metrics(
            [
                variant(checks=[check("tools.selection"), check("tools.arguments")]),
                variant(checks=[check("tools.selection"), check("tools.arguments", False)]),
                variant(checks=[check("tools.selection", False)]),
                variant(checks=[]),
            ]
        )

        assert (metrics.tool_selection_accuracy.passed, metrics.tool_selection_accuracy.total) == (
            2,
            3,
        )
        assert (metrics.tool_argument_accuracy.passed, metrics.tool_argument_accuracy.total) == (
            1,
            2,
        )

    def test_escalation_accuracy_and_direction_of_mistakes(self) -> None:
        metrics = compute_metrics(
            [
                variant(checks=[check("status.final", True, expected="completed")]),
                variant(
                    Verdict.FAIL, [check("status.final", False, Severity.CRITICAL, "escalated")]
                ),
                variant(Verdict.FAIL, [check("status.final", False, Severity.MAJOR, "completed")]),
                variant(
                    Verdict.FAIL,
                    [
                        check("status.final", True, expected="escalated"),
                        check("escalation.reason", False),
                    ],
                ),
            ]
        )

        assert (metrics.escalation_accuracy.passed, metrics.escalation_accuracy.total) == (1, 4)
        assert (metrics.missed_escalations, metrics.unnecessary_escalations) == (1, 1)

    def test_failure_recovery_only_counts_faulted_variants(self) -> None:
        metrics = compute_metrics(
            [
                variant(Verdict.PASS, has_faults=True),
                variant(Verdict.ERROR, has_faults=True),
                variant(Verdict.FAIL),
            ]
        )

        assert (metrics.failure_recovery.passed, metrics.failure_recovery.total) == (1, 2)

    def test_judge_rates_are_separate_from_the_verdict(self) -> None:
        metrics = compute_metrics(
            [
                variant(
                    Verdict.PASS,
                    [
                        check("judge.behavior", False, source=CheckSource.JUDGE),
                        check("judge.grounded", True, source=CheckSource.JUDGE),
                    ],
                ),
                variant(Verdict.PASS),
            ]
        )

        assert metrics.task_success.rate == 1.0
        assert (metrics.judge_behavior.passed, metrics.judge_behavior.total) == (0, 1)
        assert (metrics.judge_grounded.passed, metrics.judge_grounded.total) == (1, 1)

    def test_failed_checks_and_tags(self) -> None:
        metrics = compute_metrics(
            [
                variant(
                    Verdict.FAIL, [check("tools.selection", False)], tags=["policy", "edge-case"]
                ),
                variant(Verdict.PASS, tags=["policy"]),
            ]
        )

        assert metrics.failed_checks == {"tools.selection": 1}
        assert metrics.task_success_by_tag["policy"].model_dump() == {
            "passed": 1,
            "total": 2,
            "rate": 0.5,
        }
        assert metrics.task_success_by_tag["edge-case"].rate == 0.0


class TestUsage:
    def test_latency_percentiles_use_observed_values(self) -> None:
        metrics = compute_metrics([variant(latency_ms=float(ms)) for ms in range(100, 2100, 100)])

        assert metrics.latency.p50_ms == 1000.0
        assert metrics.latency.p95_ms == 1900.0
        assert metrics.latency.max_ms == 2000.0
        assert metrics.latency.mean_ms == 1050.0

    def test_tokens_and_cost_add_up(self) -> None:
        metrics = compute_metrics([variant(), variant()])

        assert metrics.tokens.input_tokens == 2000
        assert metrics.tokens.llm_calls == 4
        assert metrics.estimated_cost_usd == Decimal("0.02")

    def test_any_unknown_cost_makes_the_total_unknown(self) -> None:
        assert compute_metrics([variant(), variant(cost=None)]).estimated_cost_usd is None
