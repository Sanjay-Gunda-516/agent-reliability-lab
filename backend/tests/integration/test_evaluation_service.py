from typing import Any

import pytest
from sqlalchemy import select

from app.agent.config import AgentConfig
from app.db.lab_models import EvaluationCheckRecord, EvaluationResultRecord, TestRunRecord
from app.db.session import get_sessionmaker
from app.evaluation import service
from app.evaluation.judge import JudgeRequest, JudgeResult, JudgeVerdict
from app.evaluation.metrics import compute_metrics
from app.evaluation.service import RunSelection, SelectionError, create_test_run, execute_test_run
from app.evaluation.store import load_summaries
from tests.evaluation_helpers import FakeJudge
from tests.fakes import FakeLLM, ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration

FAST = AgentConfig(tool_retry_backoff_s=0)
GOOD_JUDGE = FakeJudge(
    JudgeVerdict(behavior="pass", behavior_reason="Fine.", unsupported_claims=[])
)


def run_suite(
    scenario_ids: list[str],
    llm: Any,
    judge: Any = None,
    personas: list[str] | None = None,
) -> TestRunRecord:
    factory = get_sessionmaker()
    with factory() as db:
        selection = RunSelection(scenario_ids=scenario_ids, personas=personas or ["normal"])
        run = create_test_run(
            db, selection, config=FAST, judge_model=judge.model if judge else None
        )
        run_id = run.id
    execute_test_run(run_id, session_factory=factory, llm=llm, judge=judge)
    with factory() as db:
        stored = db.get(TestRunRecord, run_id)
        assert stored is not None
        db.expunge(stored)
        return stored


def results_of(run_id: int) -> dict[str, EvaluationResultRecord]:
    with get_sessionmaker()() as db:
        rows = db.scalars(
            select(EvaluationResultRecord).where(EvaluationResultRecord.test_run_id == run_id)
        ).all()
        for row in rows:
            _ = row.checks, row.trace  # load before the session closes
        return {row.scenario_id: row for row in rows}


def failed_checks(result: EvaluationResultRecord) -> dict[str, EvaluationCheckRecord]:
    return {c.name: c for c in result.checks if not c.passed}


CORRECT_AGENT = ScriptedByMessage(
    {
        "4821": [
            calls(tool_use("get_order", {"order_id": 4821})),
            calls(tool_use("cancel_order", {"order_id": 4821})),
            text("Order #4821 is cancelled."),
        ],
        "8821": [
            calls(tool_use("get_order", {"order_id": 8821})),
            text("Order #8821 has already shipped, so it can't be cancelled."),
        ],
    }
)


def test_correct_agent_passes_and_everything_is_stored() -> None:
    run = run_suite(["cancel-eligible-order", "cancel-shipped-order"], CORRECT_AGENT, GOOD_JUDGE)

    assert run.status == "completed" and run.error is None
    assert run.variant_count == 2
    results = results_of(run.id)
    assert {r.verdict for r in results.values()} == {"pass"}

    cancel = results["cancel-eligible-order"]
    assert cancel.final_status == "completed"
    assert cancel.judge_verdict == "pass" and cancel.judge_model == "fake-judge"
    assert cancel.tool_calls == 2 and cancel.llm_calls == 4  # lookup, cancel, reply + intent
    assert cancel.trace is not None
    assert (
        cancel.trace.initial_state and cancel.trace.initial_state["orders"]["4821"] == "processing"
    )
    assert cancel.trace.final_state and cancel.trace.final_state["orders"]["4821"] == "cancelled"
    assert {c.name for c in cancel.checks} >= {
        "tools.selection",
        "tools.lookup_before_action",
        "state.expected",
        "judge.behavior",
    }

    assert run.metrics is not None
    assert run.metrics["task_success"] == {"passed": 2, "total": 2, "rate": 1.0}
    assert run.metrics["judge_behavior"]["total"] == 2


def test_failures_explain_what_went_wrong() -> None:
    lazy = ScriptedByMessage({"4821": [text("Sure, done!")]})

    run = run_suite(["cancel-eligible-order"], lazy)

    result = results_of(run.id)["cancel-eligible-order"]
    assert result.verdict == "fail"
    assert (result.critical_failures, result.major_failures) == (0, 3)
    failed = failed_checks(result)
    # Claims success without doing anything, and doesn't name the order either.
    assert {n for n, c in failed.items() if c.severity != "minor"} == {
        "tools.selection",
        "state.expected",
        "response.must_mention",
    }
    assert failed["intent"].severity == "minor"  # the fake labels every message "other"
    assert failed["state.expected"].expected == "cancelled"
    assert failed["state.expected"].actual == "processing"
    assert run.metrics and run.metrics["failed_checks"] == {
        "intent": 1,
        "response.must_mention": 1,
        "state.expected": 1,
        "tools.selection": 1,
    }


def test_forbidden_action_is_a_critical_failure() -> None:
    reckless = ScriptedByMessage(
        {"8821": [calls(tool_use("cancel_order", {"order_id": 8821})), text("Tried it.")]}
    )

    run = run_suite(["cancel-shipped-order"], reckless)

    result = results_of(run.id)["cancel-shipped-order"]
    assert result.verdict == "fail" and result.critical_failures == 1
    forbidden = failed_checks(result)["tools.forbidden"]
    assert forbidden.severity == "critical"
    assert forbidden.actual == [
        {"tool": "cancel_order", "input": {"order_id": 8821}, "status": "rejected"}
    ]
    assert run.metrics and run.metrics["policy_compliance"]["rate"] == 0.0


def test_crashed_variant_is_an_error_and_the_run_still_completes() -> None:
    run = run_suite(["order-status-shipped"], FakeLLM([]))

    result = results_of(run.id)["order-status-shipped"]
    assert run.status == "completed"
    assert result.verdict == "error"
    assert result.error and "ran out of scripted responses" in result.error
    assert [c.name for c in result.checks] == ["execution.crash"]
    assert run.metrics and run.metrics["crash_rate"]["passed"] == 1


def test_judge_crash_is_recorded_without_changing_the_verdict() -> None:
    class BrokenJudge(FakeJudge):
        def judge(self, request: JudgeRequest) -> JudgeResult:
            raise RuntimeError("judge exploded")

    run = run_suite(["cancel-eligible-order"], CORRECT_AGENT, BrokenJudge())

    result = results_of(run.id)["cancel-eligible-order"]
    assert result.verdict == "pass"
    assert result.judge_verdict is None
    assert result.judge_error and "judge exploded" in result.judge_error


def test_unexpected_failure_marks_the_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError("database went away")

    monkeypatch.setattr(service, "run_scenario", explode)

    run = run_suite(["cancel-eligible-order"], CORRECT_AGENT)

    assert run.status == "failed"
    assert run.error == "RuntimeError: database went away"
    assert run.finished_at is not None


def test_stored_metrics_match_metrics_recomputed_from_rows() -> None:
    run = run_suite(
        ["cancel-eligible-order", "cancel-shipped-order"],
        CORRECT_AGENT,
        personas=["normal", "angry"],
    )

    with get_sessionmaker()() as db:
        recomputed = compute_metrics(load_summaries(db, run.id)).model_dump(mode="json")
    assert recomputed == run.metrics
    assert run.variant_count == 4


class TestSelection:
    def test_unknown_scenario_is_rejected(self) -> None:
        with get_sessionmaker()() as db, pytest.raises(SelectionError, match="no-such-scenario"):
            create_test_run(
                db, RunSelection(scenario_ids=["no-such-scenario"]), config=FAST, judge_model=None
            )

    def test_selection_matching_nothing_is_rejected(self) -> None:
        empty = RunSelection(scenario_ids=["cancel-eligible-order"], personas=["incomplete"])

        with get_sessionmaker()() as db, pytest.raises(SelectionError, match="matches no"):
            create_test_run(db, empty, config=FAST, judge_model=None)


class TestVariantSelection:
    def test_picks_exact_variants_across_scenarios(self) -> None:
        selection = RunSelection(
            variant_ids=["cancel-eligible-order:normal", "other-customers-order:adversarial"]
        )

        cases = service.preview_cases(selection)

        assert {f"{s.id}:{p}" for s, p in cases} == {
            "cancel-eligible-order:normal",
            "other-customers-order:adversarial",
        }

    def test_unknown_variant_is_rejected(self) -> None:
        with pytest.raises(SelectionError, match="cancel-eligible-order:robot"):
            service.preview_cases(RunSelection(variant_ids=["cancel-eligible-order:robot"]))
