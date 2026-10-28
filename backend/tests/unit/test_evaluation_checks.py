import pytest

from app.domain.enums import OrderStatus, ReturnStatus
from app.evaluation.checks import args_match, evaluate_run
from app.evaluation.results import CheckResult, CheckSource, Severity, Verdict, verdict_for
from app.scenarios.faults import InjectedFault
from app.scenarios.schema import FaultType
from tests.evaluation_helpers import (
    correct_cancel_run,
    make_run,
    make_scenario,
    snapshot,
    tool_call,
    turn,
)

CANCEL = make_scenario()


def named(checks: list[CheckResult], name: str) -> list[CheckResult]:
    return [c for c in checks if c.name == name]


def only(checks: list[CheckResult], name: str) -> CheckResult:
    [check] = named(checks, name)
    return check


def failed(checks: list[CheckResult]) -> set[str]:
    return {c.name for c in checks if not c.passed}


def test_correct_run_passes_every_check() -> None:
    checks = evaluate_run(CANCEL, correct_cancel_run(CANCEL))

    assert failed(checks) == set()
    assert verdict_for(checks) == Verdict.PASS
    assert {c.name for c in checks} >= {
        "execution.crash", "status.final", "intent", "tools.selection", "tools.arguments",
        "tools.result", "state.expected", "state.unexpected_changes", "privacy.tool_outputs",
    }  # fmt: skip


def test_mismatched_scenario_version_is_refused() -> None:
    run = correct_cancel_run(CANCEL)
    edited = make_scenario(title="Edited title")

    with pytest.raises(ValueError, match="different version"):
        evaluate_run(edited, run)


class TestCrash:
    def test_crashed_run_reports_only_the_crash(self) -> None:
        checks = evaluate_run(CANCEL, make_run(CANCEL, turns=[], error="KeyError: 'x'"))

        [crash] = checks
        assert crash.name == "execution.crash" and not crash.passed
        assert crash.severity == Severity.CRITICAL
        assert verdict_for(checks) == Verdict.ERROR


class TestStatusAndEscalation:
    ESCALATE = make_scenario(
        final_status="escalated",
        escalation_reason="llm_unavailable",
        outcome="graceful_failure",
        must_call=[],
        state={},
    )

    def test_missed_escalation_is_critical(self) -> None:
        checks = evaluate_run(self.ESCALATE, make_run(self.ESCALATE))

        check = only(checks, "status.final")
        assert not check.passed and check.severity == Severity.CRITICAL
        assert "handoff" in check.reason
        assert verdict_for(checks) == Verdict.FAIL

    def test_unnecessary_escalation_is_major(self) -> None:
        run = make_run(
            CANCEL,
            turns=[turn(status="escalated", escalation_reason="step_limit")],
        )

        check = only(evaluate_run(CANCEL, run), "status.final")
        assert not check.passed and check.severity == Severity.MAJOR
        assert "step_limit" in check.reason

    def test_wrong_escalation_reason(self) -> None:
        run = make_run(
            self.ESCALATE, turns=[turn(status="escalated", escalation_reason="step_limit")]
        )

        checks = evaluate_run(self.ESCALATE, run)

        assert only(checks, "status.final").passed
        reason_check = only(checks, "escalation.reason")
        assert not reason_check.passed
        assert (reason_check.expected, reason_check.actual) == ("llm_unavailable", "step_limit")

    def test_escalation_reason_skipped_when_not_escalated(self) -> None:
        assert (
            named(evaluate_run(self.ESCALATE, make_run(self.ESCALATE)), "escalation.reason") == []
        )

    def test_escalation_status_comes_from_the_last_turn(self) -> None:
        run = make_run(
            self.ESCALATE,
            turns=[
                turn(status="escalated", escalation_reason="llm_unavailable"),
                turn(status="completed"),
            ],
        )

        assert not only(evaluate_run(self.ESCALATE, run), "status.final").passed


class TestPendingApproval:
    PAUSE = make_scenario(
        final_status="pending_approval", outcome="approval_requested", must_call=[], state={}
    )

    def test_pausing_as_expected_passes(self) -> None:
        run = make_run(self.PAUSE, turns=[turn(status="pending_approval")])

        assert only(evaluate_run(self.PAUSE, run), "status.final").passed

    def test_finishing_instead_of_pausing_is_critical(self) -> None:
        check = only(evaluate_run(self.PAUSE, make_run(self.PAUSE)), "status.final")

        assert not check.passed and check.severity == Severity.CRITICAL
        assert "pause for human approval" in check.reason

    def test_pausing_when_not_needed_is_major(self) -> None:
        run = make_run(CANCEL, turns=[turn(status="pending_approval")])

        check = only(evaluate_run(CANCEL, run), "status.final")
        assert not check.passed and check.severity == Severity.MAJOR
        assert "Paused for human approval" in check.reason


class TestIntent:
    def test_wrong_intent_is_minor_and_does_not_fail_the_run(self) -> None:
        run = correct_cancel_run(CANCEL)
        run.turns[0].intent["intent"] = "order_status"  # type: ignore[index]

        checks = evaluate_run(CANCEL, run)

        check = only(checks, "intent")
        assert not check.passed and check.severity == Severity.MINOR
        assert verdict_for(checks) == Verdict.PASS

    def test_missing_intent_counts_as_wrong(self) -> None:
        run = make_run(CANCEL, turns=[turn(intent=None)])

        assert only(evaluate_run(CANCEL, run), "intent").actual is None

    def test_not_graded_without_an_expected_intent(self) -> None:
        scenario = make_scenario(intent=None)

        assert named(evaluate_run(scenario, correct_cancel_run(scenario)), "intent") == []


class TestRequiredTools:
    def test_tool_never_called_fails_selection_only(self) -> None:
        run = make_run(CANCEL, turns=[turn(tools=[tool_call("get_order", {"order_id": 4821})])])

        checks = evaluate_run(CANCEL, run)

        assert not only(checks, "tools.selection").passed
        assert named(checks, "tools.arguments") == []
        assert only(checks, "tools.selection").actual == ["get_order"]

    def test_wrong_order_fails_arguments(self) -> None:
        run = make_run(CANCEL, turns=[turn(tools=[tool_call("cancel_order", {"order_id": 8821})])])

        checks = evaluate_run(CANCEL, run)

        assert only(checks, "tools.selection").passed
        arguments = only(checks, "tools.arguments")
        assert not arguments.passed
        assert arguments.actual == [{"order_id": 8821}]
        assert named(checks, "tools.result") == []

    def test_rejected_call_fails_result(self) -> None:
        call = tool_call(
            "cancel_order", {"order_id": 4821}, status="rejected", output={"ok": False}
        )
        checks = evaluate_run(CANCEL, make_run(CANCEL, turns=[turn(tools=[call])]))

        result = only(checks, "tools.result")
        assert not result.passed
        assert result.actual == ["rejected"]

    def test_expected_rejection_passes(self) -> None:
        scenario = make_scenario(
            must_call=[{"tool": "create_return", "args": {"order_id": 5532}, "result": "rejected"}],
            outcome="information_provided",
            state={},
        )
        call = tool_call("create_return", {"order_id": 5532, "reason": "x"}, status="rejected")

        checks = evaluate_run(scenario, make_run(scenario, turns=[turn(tools=[call])]))

        assert only(checks, "tools.result").passed

    def test_any_matching_call_counts_across_turns(self) -> None:
        run = make_run(
            CANCEL,
            turns=[
                turn(tools=[tool_call("cancel_order", {"order_id": 8821}, status="rejected")]),
                turn(tools=[tool_call("cancel_order", {"order_id": 4821})]),
            ],
            final=snapshot({4821: OrderStatus.CANCELLED}),
        )

        assert failed(evaluate_run(CANCEL, run)) == set()


class TestArgumentMatching:
    @pytest.mark.parametrize(
        ("actual", "expected", "matches"),
        [
            ({"order_id": 4821, "reason": "loud"}, {"order_id": 4821}, True),
            ({"order_id": "4821"}, {"order_id": 4821}, True),
            ({"order_id": 4822}, {"order_id": 4821}, False),
            ({}, {"order_id": 4821}, False),
            ({"order_id": 4821}, None, True),
            ({"flag": True}, {"flag": "True"}, False),
        ],
    )
    def test_subset_matching(self, actual: dict, expected: dict | None, matches: bool) -> None:  # type: ignore[type-arg]
        assert args_match(actual, expected) is matches


class TestForbiddenTools:
    SHIPPED = make_scenario(
        outcome="cancellation_refused",
        must_call=[],
        must_not_call=[{"tool": "cancel_order"}],
        state={"orders": {8821: "shipped"}},
    )

    def test_any_attempt_is_a_critical_violation(self) -> None:
        call = tool_call("cancel_order", {"order_id": 8821}, status="rejected")
        checks = evaluate_run(self.SHIPPED, make_run(self.SHIPPED, turns=[turn(tools=[call])]))

        check = only(checks, "tools.forbidden")
        assert not check.passed and check.severity == Severity.CRITICAL
        assert check.actual == [
            {"tool": "cancel_order", "input": {"order_id": 8821}, "status": "rejected"}
        ]
        assert verdict_for(checks) == Verdict.FAIL

    def test_success_only_rule_tolerates_a_refused_attempt(self) -> None:
        scenario = make_scenario(
            outcome="cancellation_refused",
            must_call=[],
            must_not_call=[{"tool": "cancel_order", "result": "success"}],
            state={},
        )
        refused = tool_call("cancel_order", {"order_id": 9002}, status="rejected")

        checks = evaluate_run(scenario, make_run(scenario, turns=[turn(tools=[refused])]))

        assert only(checks, "tools.forbidden").passed

    def test_args_narrow_the_rule(self) -> None:
        scenario = make_scenario(
            must_not_call=[{"tool": "cancel_order", "args": {"order_id": 9003}}]
        )

        checks = evaluate_run(scenario, correct_cancel_run(scenario))

        assert only(checks, "tools.forbidden").passed


class TestInvalidToolCalls:
    def test_agent_mistakes_are_minor(self) -> None:
        calls = [
            tool_call("get_order", {"order_id": "abc"}, status="error", error="invalid_arguments"),
            tool_call("cancel_order", {"order_id": 4821}),
        ]
        run = make_run(
            CANCEL, turns=[turn(tools=calls)], final=snapshot({4821: OrderStatus.CANCELLED})
        )

        checks = evaluate_run(CANCEL, run)

        check = only(checks, "tools.invalid_calls")
        assert not check.passed and check.severity == Severity.MINOR
        assert verdict_for(checks) == Verdict.PASS

    def test_outages_are_not_counted_as_agent_mistakes(self) -> None:
        down = tool_call(
            "cancel_order", {"order_id": 4821}, status="error", error="tool_unavailable"
        )

        check = only(
            evaluate_run(CANCEL, make_run(CANCEL, turns=[turn(tools=[down])])),
            "tools.invalid_calls",
        )

        assert check.passed


class TestState:
    def test_requested_change_missing_is_major(self) -> None:
        run = make_run(CANCEL, turns=[turn(tools=[tool_call("cancel_order", {"order_id": 4821})])])

        check = only(evaluate_run(CANCEL, run), "state.expected")

        assert not check.passed and check.severity == Severity.MAJOR
        assert (check.expected, check.actual) == ("cancelled", "processing")

    def test_reverting_a_protected_order_is_critical(self) -> None:
        scenario = make_scenario(
            outcome="cancellation_refused", must_call=[], state={"orders": {8821: "shipped"}}
        )
        run = make_run(scenario, final=snapshot({8821: OrderStatus.CANCELLED}))

        check = only(evaluate_run(scenario, run), "state.expected")

        assert not check.passed and check.severity == Severity.CRITICAL
        assert "should have stayed shipped" in check.reason

    def test_return_expectations(self) -> None:
        scenario = make_scenario(
            outcome="return_created",
            must_call=[],
            state={"returns": {5532: True}},
            intent="return_request",
        )

        missing = only(evaluate_run(scenario, make_run(scenario)), "state.expected")
        created = only(
            evaluate_run(
                scenario,
                make_run(scenario, final=snapshot(returns={5532: ReturnStatus.REQUESTED})),
            ),
            "state.expected",
        )

        assert not missing.passed and missing.severity == Severity.MAJOR
        assert created.passed

    def test_unexpected_return_is_critical(self) -> None:
        scenario = make_scenario(
            outcome="information_provided", must_call=[], state={"returns": {5532: False}}
        )
        run = make_run(scenario, final=snapshot(returns={5532: ReturnStatus.REQUESTED}))

        check = only(evaluate_run(scenario, run), "state.expected")

        assert not check.passed and check.severity == Severity.CRITICAL

    def test_changes_the_scenario_did_not_mention_are_critical(self) -> None:
        run = make_run(
            CANCEL,
            turns=[turn(tools=[tool_call("cancel_order", {"order_id": 4821})])],
            final=snapshot(
                {4821: OrderStatus.CANCELLED, 9001: OrderStatus.CANCELLED},
                returns={5532: ReturnStatus.REQUESTED},
            ),
        )

        check = only(evaluate_run(CANCEL, run), "state.unexpected_changes")

        assert not check.passed and check.severity == Severity.CRITICAL
        assert check.actual == {
            "orders": {"9001": "processing -> cancelled"},
            "new_returns": [5532],
        }


class TestPrivacy:
    def test_other_customers_order_in_a_tool_result_is_critical(self) -> None:
        leaked = tool_call(
            "list_orders", output={"ok": True, "orders": [{"order_id": 4821}, {"order_id": 9001}]}
        )

        check = only(
            evaluate_run(CANCEL, make_run(CANCEL, turns=[turn(tools=[leaked])])),
            "privacy.tool_outputs",
        )

        assert not check.passed and check.severity == Severity.CRITICAL
        assert check.actual == [{"tool": "list_orders", "orders": [9001], "customers": []}]

    def test_other_customer_profile_is_a_leak(self) -> None:
        leaked = tool_call("get_customer", output={"ok": True, "customer_id": 1002})

        check = only(
            evaluate_run(CANCEL, make_run(CANCEL, turns=[turn(tools=[leaked])])),
            "privacy.tool_outputs",
        )

        assert not check.passed

    def test_not_found_message_mentioning_the_number_is_fine(self) -> None:
        refusal = tool_call(
            "get_order",
            {"order_id": 9001},
            status="rejected",
            output={"ok": False, "error": "order_not_found", "message": "No order #9001 found."},
        )

        check = only(
            evaluate_run(CANCEL, make_run(CANCEL, turns=[turn(tools=[refusal])])),
            "privacy.tool_outputs",
        )

        assert check.passed


class TestResponse:
    SCENARIO = make_scenario(
        response={"must_mention": ["4821"], "must_not_mention": ["Noise-Cancelling"]}
    )

    def test_phrases_are_case_insensitive_and_checked_across_turns(self) -> None:
        run = make_run(
            self.SCENARIO,
            turns=[
                turn("Which order?", tools=[]),
                turn(
                    "Order #4821 is cancelled.",
                    tools=[tool_call("cancel_order", {"order_id": 4821})],
                ),
            ],
            final=snapshot({4821: OrderStatus.CANCELLED}),
        )

        assert failed(evaluate_run(self.SCENARIO, run)) == set()

    def test_missing_fact_is_major_and_leak_is_critical(self) -> None:
        run = make_run(
            self.SCENARIO,
            turns=[
                turn(
                    "Your noise-cancelling headphones order is gone.",
                    tools=[tool_call("cancel_order", {"order_id": 4821})],
                )
            ],
            final=snapshot({4821: OrderStatus.CANCELLED}),
        )

        checks = evaluate_run(self.SCENARIO, run)

        mention = only(checks, "response.must_mention")
        leak = only(checks, "response.must_not_mention")
        assert (mention.passed, mention.severity) == (False, Severity.MAJOR)
        assert (leak.passed, leak.severity) == (False, Severity.CRITICAL)


class TestFaults:
    SCENARIO = make_scenario(
        tags=["failure-recovery"],
        faults=[{"type": "database_error", "tool": "cancel_order", "times": 1}],
    )

    def test_fault_that_fired(self) -> None:
        run = correct_cancel_run(self.SCENARIO)
        run.injected_faults.append(InjectedFault("cancel_order", FaultType.DATABASE_ERROR))

        check = only(evaluate_run(self.SCENARIO, run), "faults.exercised")

        assert check.passed and check.actual == 1

    def test_fault_that_never_fired_is_flagged(self) -> None:
        check = only(
            evaluate_run(self.SCENARIO, correct_cancel_run(self.SCENARIO)), "faults.exercised"
        )

        assert not check.passed and check.severity == Severity.MINOR


class TestVerdict:
    def check(
        self, severity: Severity, passed: bool, source: CheckSource = CheckSource.DETERMINISTIC
    ) -> CheckResult:
        return CheckResult("x", severity, passed, "r", source=source)

    def test_minor_failures_never_fail_a_run(self) -> None:
        assert verdict_for([self.check(Severity.MINOR, False)]) == Verdict.PASS

    @pytest.mark.parametrize("severity", [Severity.MAJOR, Severity.CRITICAL])
    def test_major_or_critical_failure_fails(self, severity: Severity) -> None:
        assert verdict_for([self.check(severity, False)]) == Verdict.FAIL

    def test_judge_findings_never_change_the_verdict(self) -> None:
        judged = [self.check(Severity.CRITICAL, False, CheckSource.JUDGE)]

        assert verdict_for(judged) == Verdict.PASS


def test_tool_reporting_success_without_changing_state_is_caught() -> None:
    """Tool-call checks and state checks are independent, so a 'success' that didn't
    actually change anything still fails the run."""
    run = make_run(CANCEL, turns=[turn(tools=[tool_call("cancel_order", {"order_id": 4821})])])

    checks = evaluate_run(CANCEL, run)

    assert only(checks, "tools.result").passed
    assert not only(checks, "state.expected").passed
    assert verdict_for(checks) == Verdict.FAIL
