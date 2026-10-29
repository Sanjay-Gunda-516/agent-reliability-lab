"""Regression comparisons end to end with a scripted model: runs, reports, reruns, CLI."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import agent as agent_api
from app.config import Settings
from app.db.lab_models import (
    EvaluationResultRecord,
    RegressionReportRecord,
    RegressionVariantRecord,
    TestRunRecord,
)
from app.db.session import get_sessionmaker
from app.evaluation import __main__ as cli
from app.main import app
from tests.fakes import ScriptedByMessage, calls, text, tool_use

pytestmark = pytest.mark.integration

CANCEL_4821 = [
    calls(tool_use("get_order", {"order_id": 4821})),
    calls(tool_use("cancel_order", {"order_id": 4821})),
    text("Order #4821 is cancelled."),
]
GOOD = {
    "4821": CANCEL_4821,
    "8821": [
        calls(tool_use("get_order", {"order_id": 8821})),
        text("Order #8821 has shipped, so it can't be cancelled. You can return it on arrival."),
    ],
}
# The candidate cancels a shipped order: a forbidden call, so a critical failure.
BAD = {**GOOD, "8821": [calls(tool_use("cancel_order", {"order_id": 8821})), text("Done.")]}
SCENARIOS = ["cancel-eligible-order", "cancel-shipped-order"]
SHIPPED = "cancel-shipped-order:normal"


@pytest.fixture
def api(client: TestClient) -> Iterator[TestClient]:
    yield client


def use_llm(scripts: dict[str, list[Any]]) -> None:
    app.dependency_overrides[agent_api.get_llm_client] = lambda: ScriptedByMessage(scripts)


def test_run(
    api: TestClient,
    scripts: dict[str, list[Any]],
    agent_version: str = "v1",
    scenario_ids: list[str] | None = None,
) -> int:
    use_llm(scripts)
    response = api.post(
        "/test-runs",
        json={
            "scenario_ids": scenario_ids or SCENARIOS,
            "personas": ["normal"],
            "agent_version": agent_version,
        },
    )
    assert response.status_code == 202, response.text
    run_id: int = response.json()["id"]
    assert api.get(f"/test-runs/{run_id}").json()["status"] == "completed"
    return run_id


test_run.__test__ = False  # type: ignore[attr-defined]


def compare(api: TestClient, baseline: int, candidate: int, **body: Any) -> Any:
    return api.post(
        "/comparisons",
        json={"baseline_run_id": baseline, "candidate_run_id": candidate, **body},
    )


def regressed_pair(api: TestClient) -> tuple[int, int, dict[str, Any]]:
    baseline = test_run(api, GOOD)
    candidate = test_run(api, BAD, "v1-regressed")
    response = compare(api, baseline, candidate)
    assert response.status_code == 201, response.text
    return baseline, candidate, response.json()


def variant(report: dict[str, Any], variant_id: str = SHIPPED) -> dict[str, Any]:
    return next(v for v in report["variants"] if v["variant_id"] == variant_id)


class TestComparison:
    def test_regression_report_is_stored_and_fails_the_gate(self, api: TestClient) -> None:
        baseline, candidate, report = regressed_pair(api)

        assert (report["baseline_run_id"], report["candidate_run_id"]) == (baseline, candidate)
        assert (report["baseline_agent_version"], report["candidate_agent_version"]) == (
            "v1",
            "v1-regressed",
        )
        assert report["revision"] == 1 and report["revisions"] == [report["id"]]
        assert report["gate_status"] == "fail"
        codes = {r["code"]: r["level"] for r in report["gate"]["reasons"]}
        assert codes["new_critical_failures"] == "fail"
        assert codes["task_success_drop"] == "fail"  # 2/2 -> 1/2
        assert codes["rerun_recommended"] == "warn"
        shipped = variant(report)
        assert shipped["category"] == "regression" and shipped["needs_rerun"]
        assert shipped["new_critical_failures"] == ["tools.forbidden[cancel_order]"]
        assert (shipped["baseline_verdict"], shipped["candidate_verdict"]) == ("pass", "fail")
        assert variant(report, "cancel-eligible-order:normal")["category"] == "unchanged"
        task = next(d for d in report["metric_deltas"] if d["name"] == "task_success")
        assert (task["baseline_detail"], task["candidate_detail"], task["delta"]) == (
            "2/2",
            "1/2",
            -50.0,
        )
        assert report["counts"]["regression"] == 1 and report["counts"]["unchanged"] == 1

        stored = api.get(f"/comparisons/{report['id']}").json()
        assert stored == report
        with get_sessionmaker()() as db:
            rows = db.scalars(
                select(RegressionVariantRecord).where(
                    RegressionVariantRecord.report_id == report["id"]
                )
            ).all()
            assert {r.variant_id: r.category for r in rows} == {
                "cancel-eligible-order:normal": "unchanged",
                SHIPPED: "regression",
            }
            assert all(r.baseline_result_id and r.candidate_result_id for r in rows)

    def test_gate_endpoint_is_machine_and_human_readable(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)

        gate = api.get(f"/comparisons/{report['id']}/gate").json()

        assert gate["status"] == "fail"
        assert gate["thresholds"]["max_task_success_drop_pp"] == "5"
        assert gate["text"].splitlines()[1] == "GATE: FAIL"
        assert "[FAIL] new_critical_failures" in gate["text"]
        assert f"regression            {SHIPPED}" in gate["text"]

    def test_identical_behaviour_passes(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, GOOD, "v1-regressed")

        # Scripted runs take a few real milliseconds, so latency is too noisy to compare.
        report = compare(
            api, baseline, candidate, thresholds={"max_latency_increase_pct": 100_000}
        ).json()

        # The fake model has no price, so cost can't be compared: a warning, not a pass.
        assert report["gate_status"] == "warn"
        assert {r["code"] for r in report["gate"]["reasons"]} == {"cost_unknown"}

    def test_improvement_is_detected(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, BAD), test_run(api, GOOD)

        report = compare(api, baseline, candidate).json()

        improved = variant(report)
        assert improved["category"] == "improvement" and not improved["needs_rerun"]
        assert improved["resolved_critical_failures"] == ["tools.forbidden[cancel_order]"]

    def test_custom_thresholds_are_stored_with_the_report(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, BAD)

        report = compare(
            api, baseline, candidate,
            thresholds={"fail_on_new_critical": False, "max_task_success_drop_pp": 60},
        ).json()  # fmt: skip

        assert report["gate_status"] == "warn"
        assert report["thresholds"]["max_task_success_drop_pp"] == "60"
        assert {r["code"]: r["level"] for r in report["gate"]["reasons"]}[
            "new_critical_failures"
        ] == "warn"

    def test_changed_scenario_content_is_flagged_not_blamed(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, BAD)
        with get_sessionmaker()() as db:  # as if the scenario file was edited in between
            result = db.scalars(
                select(EvaluationResultRecord).where(
                    EvaluationResultRecord.test_run_id == candidate,
                    EvaluationResultRecord.variant_id == SHIPPED,
                )
            ).one()
            result.scenario_hash = "0" * 64
            db.commit()

        report = compare(api, baseline, candidate).json()

        changed = variant(report)
        assert changed["category"] == "scenario_changed" and not changed["gated"]
        codes = {r["code"] for r in report["gate"]["reasons"]}
        assert "scenario_changed" in codes and "new_critical_failures" not in codes

    def test_variants_filter_and_list_filters(self, api: TestClient) -> None:
        baseline, candidate, report = regressed_pair(api)

        regressions = api.get(
            f"/comparisons/{report['id']}/variants", params={"category": "regression"}
        ).json()
        listed = api.get("/comparisons", params={"candidate_run_id": candidate}).json()

        assert [v["variant_id"] for v in regressions] == [SHIPPED]
        assert [r["id"] for r in listed] == [report["id"]]
        assert api.get("/comparisons", params={"baseline_run_id": candidate}).json() == []

    def test_acting_before_looking_up_is_caught_as_a_regression(self, api: TestClient) -> None:
        blind = {**GOOD, "4821": [calls(tool_use("cancel_order", {"order_id": 4821})),
                                  text("Order #4821 is cancelled.")]}  # fmt: skip
        baseline, candidate = test_run(api, GOOD), test_run(api, blind)

        report = compare(api, baseline, candidate).json()

        eligible = variant(report, "cancel-eligible-order:normal")
        assert eligible["category"] == "regression"
        assert eligible["new_failed_checks"] == [
            "tools.lookup_before_action[cancel_order(order_id=4821)]"
        ]
        assert eligible["new_critical_failures"] == []  # major: the store rules still apply
        codes = {r["code"]: r["level"] for r in report["gate"]["reasons"]}
        assert codes["task_success_drop"] == "fail" and "new_critical_failures" not in codes


class TestIncompatible:
    def test_unknown_runs_and_reports(self, api: TestClient) -> None:
        run_id = test_run(api, GOOD)

        assert compare(api, run_id, 999999).status_code == 404
        assert compare(api, 999999, run_id).status_code == 404
        assert api.get("/comparisons/999999").status_code == 404
        assert api.get("/comparisons/999999/gate").status_code == 404

    def test_same_run_is_rejected(self, api: TestClient) -> None:
        run_id = test_run(api, GOOD)

        response = compare(api, run_id, run_id)

        assert response.status_code == 422 and "same run" in response.text

    def test_unfinished_run_is_rejected(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, GOOD)
        with get_sessionmaker()() as db:
            run = db.get(TestRunRecord, candidate)
            assert run is not None
            run.status = "running"
            db.commit()

        response = compare(api, baseline, candidate)

        assert response.status_code == 409 and "running" in response.text

    def test_runs_without_common_variants_are_rejected(self, api: TestClient) -> None:
        baseline = test_run(api, GOOD, scenario_ids=["cancel-eligible-order"])
        candidate = test_run(api, GOOD, scenario_ids=["cancel-shipped-order"])

        response = compare(api, baseline, candidate)

        assert response.status_code == 422 and "nothing to compare" in response.text

    def test_invalid_thresholds_are_rejected(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, GOOD)

        assert (
            compare(api, baseline, candidate, thresholds={"max_task_success_drop_pp": -1})
        ).status_code == 422
        assert (compare(api, baseline, candidate, thresholds={"typo": 1})).status_code == 422


def rerun(api: TestClient, report_id: int, scripts: dict[str, list[Any]], **body: Any) -> Any:
    use_llm(scripts)
    return api.post(f"/comparisons/{report_id}/reruns", json=body)


class TestReruns:
    def test_failure_that_does_not_reproduce_is_noise(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)

        response = rerun(api, report["id"], GOOD, repetitions=3, sides="both")

        assert response.status_code == 202, response.text
        queued = response.json()
        assert queued["variant_ids"] == [SHIPPED] and len(queued["test_run_ids"]) == 6
        [done] = api.get(f"/comparisons/{report['id']}/reruns").json()
        assert done["status"] == "completed" and done["error"] is None
        revised = api.get(f"/comparisons/{done['result_report_id']}").json()
        assert revised["revision"] == 2 and revised["root_report_id"] == report["id"]
        assert revised["rerun_id"] == done["id"]
        noisy = variant(revised)
        assert noisy["category"] == "noisy" and not noisy["gated"]
        evidence = noisy["rerun_evidence"]
        assert evidence["first_category"] == "regression"
        assert (evidence["candidate_reproduced"], evidence["candidate_counted"]) == (0, 3)
        assert (evidence["baseline_reproduced"], evidence["baseline_counted"]) == (0, 3)
        assert "0/3" in noisy["explanation"]
        # With the noisy variant left out, nothing fails the gate.
        assert revised["gate_status"] == "warn"
        assert {r["code"] for r in revised["gate"]["reasons"]} >= {"noisy_variants"}
        assert "new_critical_failures" not in {r["code"] for r in revised["gate"]["reasons"]}

    def test_original_report_is_never_changed(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)

        rerun(api, report["id"], GOOD, repetitions=2)

        after = api.get(f"/comparisons/{report['id']}").json()
        assert {**after, "revisions": None} == {**report, "revisions": None}
        assert len(after["revisions"]) == 2

    def test_reproduced_failure_is_a_confirmed_regression(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)

        done = rerun(api, report["id"], BAD, repetitions=3, sides="candidate").json()

        revised_id = api.get(f"/comparisons/{report['id']}/reruns").json()[0]["result_report_id"]
        revised = api.get(f"/comparisons/{revised_id}").json()
        confirmed = variant(revised)
        assert len(done["test_run_ids"]) == 3
        assert confirmed["category"] == "confirmed_regression" and confirmed["gated"]
        assert confirmed["rerun_evidence"]["baseline_counted"] == 0
        assert revised["gate_status"] == "fail"
        assert "rerun_recommended" not in {r["code"] for r in revised["gate"]["reasons"]}

    def test_baseline_failing_too_makes_it_noise(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)

        rerun(api, report["id"], BAD, repetitions=3, sides="both")

        revised_id = api.get(f"/comparisons/{report['id']}/reruns").json()[0]["result_report_id"]
        evidence = variant(api.get(f"/comparisons/{revised_id}").json())["rerun_evidence"]
        assert evidence["outcome"] == "noisy"
        assert (evidence["baseline_reproduced"], evidence["baseline_counted"]) == (3, 3)

    def test_rerun_runs_repeat_the_original_versions(self, api: TestClient) -> None:
        baseline, candidate, report = regressed_pair(api)

        done = rerun(api, report["id"], GOOD, repetitions=2, sides="both").json()

        with get_sessionmaker()() as db:
            runs = [db.get(TestRunRecord, i) for i in done["test_run_ids"]]
            originals = {
                "candidate": db.get(TestRunRecord, candidate),
                "baseline": db.get(TestRunRecord, baseline),
            }
            for run in runs:
                assert run is not None and run.rerun_id == done["id"]
                original = originals[str(run.rerun_side)]
                assert original is not None
                assert run.agent_version == original.agent_version
                assert run.agent_config == original.agent_config
                assert run.judge_model is None
            assert sorted((r.rerun_side, r.repetition) for r in runs if r) == [
                ("baseline", 1), ("baseline", 2), ("candidate", 1), ("candidate", 2),
            ]  # fmt: skip

    def test_later_reruns_accumulate_evidence(self, api: TestClient) -> None:
        _, _, report = regressed_pair(api)
        rerun(api, report["id"], GOOD, repetitions=1, sides="candidate")

        # Rerunning from the revision still targets the original comparison.
        second_revision = api.get(f"/comparisons/{report['id']}").json()["revisions"][1]
        rerun(api, second_revision, BAD, repetitions=2, sides="candidate")

        reports = api.get(f"/comparisons/{report['id']}").json()["revisions"]
        third = api.get(f"/comparisons/{reports[2]}").json()
        evidence = variant(third)["rerun_evidence"]
        assert third["revision"] == 3
        assert (evidence["candidate_reproduced"], evidence["candidate_counted"]) == (2, 3)
        assert variant(third)["category"] == "confirmed_regression"

    def test_invalid_reruns(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, GOOD)
        same = compare(api, baseline, candidate).json()
        _, _, report = regressed_pair(api)

        assert rerun(api, same["id"], GOOD).status_code == 422  # nothing needs a rerun
        assert rerun(api, report["id"], GOOD, repetitions=0).status_code == 422
        assert rerun(api, report["id"], GOOD, repetitions=6).status_code == 422
        assert rerun(api, report["id"], GOOD, variant_ids=["nope:normal"]).status_code == 422
        assert rerun(api, report["id"], GOOD, sides="neither").status_code == 422
        assert api.post("/comparisons/999999/reruns", json={}).status_code == 404

    def test_changed_scenario_variants_cannot_be_rerun(self, api: TestClient) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, BAD)
        with get_sessionmaker()() as db:
            result = db.scalars(
                select(EvaluationResultRecord).where(
                    EvaluationResultRecord.test_run_id == candidate,
                    EvaluationResultRecord.variant_id == SHIPPED,
                )
            ).one()
            result.scenario_hash = "0" * 64
            db.commit()
        report = compare(api, baseline, candidate).json()

        response = rerun(api, report["id"], GOOD, variant_ids=[SHIPPED])

        assert response.status_code == 422 and "unchanged scenarios" in response.text


class TestAgentVersionsApi:
    def test_lists_versions(self, api: TestClient) -> None:
        listed = api.get("/agent-versions").json()
        one = api.get("/agent-versions/v1-regressed").json()

        assert {"v1", "v1-regressed"} <= {v["name"] for v in listed}
        assert one["prompt_version"] == "v1-regressed" and one["defined_in_code"] is True
        assert api.get("/agent-versions/nope").status_code == 404

    def test_test_run_with_unknown_version_is_rejected(self, api: TestClient) -> None:
        use_llm(GOOD)

        response = api.post("/test-runs", json={"agent_version": "v9"})

        assert response.status_code == 422 and "unknown agent version" in response.text


def report_count() -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(RegressionReportRecord)) or 0


class TestCli:
    def test_compare_exits_1_when_the_gate_fails(
        self, api: TestClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, BAD)

        code = cli.main(["compare", str(baseline), str(candidate)])

        out = capsys.readouterr().out
        assert code == 1 and "GATE: FAIL" in out and "new_critical_failures" in out

    def test_compare_exits_0_unless_it_fails_and_prints_json(
        self, api: TestClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        baseline, candidate = test_run(api, GOOD), test_run(api, BAD)

        code = cli.main(
            [
                "compare", str(baseline), str(candidate), "--json",
                "--warn-on-new-critical", "--max-task-success-drop", "50",
            ]
        )  # fmt: skip

        report = json.loads(capsys.readouterr().out)
        assert code == 0 and report["gate_status"] == "warn"
        assert report["thresholds"]["max_task_success_drop_pp"] == "50"
        assert {v["variant_id"]: v["category"] for v in report["variants"]}[SHIPPED] == (
            "regression"
        )

    def test_compare_errors(self, api: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
        run_id = test_run(api, GOOD)

        assert cli.main(["compare", str(run_id), "999999"]) == 2
        assert cli.main(["compare", str(run_id), str(run_id + 1), "--max-cost-increase", "-5"])
        assert "Invalid threshold" in capsys.readouterr().err

    def test_report_prints_a_stored_comparison(
        self, api: TestClient, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _, _, report = regressed_pair(api)

        assert cli.main(["report", str(report["id"])]) == 0
        assert f"Regression report {report['id']} (revision 1)" in capsys.readouterr().out
        assert cli.main(["report", "999999"]) == 1

    def test_versions_lists_code_and_legacy_versions(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["versions"]) == 0
        out = capsys.readouterr().out
        assert "v1 " in out and "v1-regressed" in out

    def test_rerun_asks_with_a_cost_estimate_and_does_nothing_when_declined(
        self,
        api: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, _, report = regressed_pair(api)
        monkeypatch.setattr(
            cli,
            "get_settings",
            lambda: Settings(_env_file=None, database_url="unused", anthropic_api_key="test"),
        )
        prompts: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
        with get_sessionmaker()() as db:
            runs_before = db.scalar(select(func.count()).select_from(TestRunRecord))
        reports_before = report_count()

        code = cli.main(["rerun", str(report["id"]), "--repetitions", "2"])

        out = capsys.readouterr().out
        assert code == 1 and "Cancelled." in out
        assert f"Rerun 1 variant(s) x 4 run(s) (both), unknown cost:\n  {SHIPPED}" in out
        assert prompts == ["This calls the real Claude API. Continue? [y/N] "]
        with get_sessionmaker()() as db:
            assert db.scalar(select(func.count()).select_from(TestRunRecord)) == runs_before
        assert report_count() == reports_before
