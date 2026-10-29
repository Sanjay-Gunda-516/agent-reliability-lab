"""Run, inspect and compare evaluations from the command line. Run from backend/:

    python -m app.evaluation versions
    python -m app.evaluation run --scenario cancel-eligible-order --agent-version v1 --judge
    python -m app.evaluation show 12
    python -m app.evaluation compare 12 14          # baseline run, candidate run
    python -m app.evaluation report 3 [--json]
    python -m app.evaluation rerun 3 --repetitions 3 --sides both

`run` and `rerun` call the real Claude API (they cost money) and ask before starting.
`compare` exits 1 when the gate fails, so it can be used as a CI check.
"""

import argparse
import json
import sys
from decimal import Decimal

from sqlalchemy import select

from app.agent.llm import ClaudeClient
from app.agent.versions import (
    DEFAULT_AGENT_VERSION,
    AgentVersionConflict,
    UnknownAgentVersion,
    get_version,
    sync_agent_versions,
)
from app.config import get_settings
from app.db.lab_models import (
    EvaluationCheckRecord,
    EvaluationResultRecord,
    RegressionReportRecord,
    RegressionRerunRecord,
    TestRunRecord,
)
from app.db.session import get_sessionmaker
from app.evaluation.comparisons import (
    ComparisonError,
    create_comparison,
    default_rerun_variants,
    estimate_rerun_cost,
    execute_rerun,
    render_report,
    request_rerun,
    root_of,
)
from app.evaluation.evaluate import EvaluatedVariant
from app.evaluation.judge import ClaudeJudge
from app.evaluation.metrics import compute_metrics
from app.evaluation.regression import GateThresholds
from app.evaluation.service import (
    RunSelection,
    SelectionError,
    create_test_run,
    execute_test_run,
    preview_cases,
)
from app.evaluation.store import load_summaries
from app.observability.logging import configure_logging


def _print_result(evaluated: EvaluatedVariant) -> None:
    summary = evaluated.summary
    cost = f"${summary.cost_usd:.4f}" if summary.cost_usd is not None else "cost unknown"
    print(
        f"  {evaluated.verdict.upper():5} {summary.variant_id:<45} "
        f"{summary.latency_ms / 1000:5.1f}s  {summary.tokens.llm_calls} calls  {cost}"
    )
    for check in evaluated.checks:
        if not check.passed:
            print(f"        - [{check.severity}/{check.source}] {check.name}: {check.reason}")


def _show(run_id: int) -> int:
    with get_sessionmaker()() as db:
        run = db.get(TestRunRecord, run_id)
        if run is None:
            print(f"No test run {run_id}", file=sys.stderr)
            return 1
        print(f"Run {run.id}: {run.status}, {run.variant_count} variants, judge={run.judge_model}")
        metrics = compute_metrics(load_summaries(db, run_id))
        print(json.dumps(metrics.model_dump(mode="json"), indent=2))
        failed = db.execute(
            select(EvaluationCheckRecord, EvaluationResultRecord.variant_id)
            .join(EvaluationResultRecord)
            .where(
                EvaluationResultRecord.test_run_id == run_id,
                EvaluationCheckRecord.passed.is_(False),
            )
        )
        for check, variant in failed:
            print(f"  {variant}: [{check.severity}/{check.source}] {check.name}: {check.reason}")
    return 0


def _versions() -> int:
    with get_sessionmaker()() as db:
        try:
            records = sync_agent_versions(db)
        except AgentVersionConflict as exc:
            print(exc, file=sys.stderr)
            return 1
        db.commit()
        for r in records:
            source = "" if r.defined_in_code else "  (only in old runs)"
            print(f"{r.name:<16} {r.model or '?':<16} prompt {r.prompt_version or '?':<14} "
                  f"{r.config_hash[:12]}  {r.description}{source}")  # fmt: skip
    return 0


def _print_report(report: RegressionReportRecord, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "id": report.id, "revision": report.revision,
            "root_report_id": report.root_report_id,
            "baseline_run_id": report.baseline_run_id,
            "candidate_run_id": report.candidate_run_id,
            "gate_status": report.gate_status, "gate": report.gate,
            "thresholds": report.thresholds, "counts": report.counts,
            "metric_deltas": report.metric_deltas,
            "variants": [
                {"variant_id": v.variant_id, "category": v.category, "gated": v.gated,
                 "needs_rerun": v.needs_rerun, "explanation": v.explanation,
                 "new_critical_failures": v.new_critical_failures,
                 "rerun_evidence": v.rerun_evidence}
                for v in report.variants
            ],
        }, indent=2))  # fmt: skip
    else:
        print(render_report(report))


def _compare(
    baseline_run_id: int, candidate_run_id: int, thresholds: GateThresholds, as_json: bool
) -> int:
    with get_sessionmaker()() as db:
        try:
            report = create_comparison(db, baseline_run_id, candidate_run_id, thresholds)
        except ComparisonError as exc:
            print(f"Can't compare: {exc}", file=sys.stderr)
            return 2
        _print_report(report, as_json)
        return 1 if report.gate_status == "fail" else 0


def _report(report_id: int, as_json: bool) -> int:
    with get_sessionmaker()() as db:
        report = db.get(RegressionReportRecord, report_id)
        if report is None:
            print(f"No comparison {report_id}", file=sys.stderr)
            return 1
        _print_report(report, as_json)
    return 0


def _rerun(args: argparse.Namespace, llm: ClaudeClient) -> int:
    factory = get_sessionmaker()
    with factory() as db:
        report = db.get(RegressionReportRecord, args.report_id)
        if report is None:
            print(f"No comparison {args.report_id}", file=sys.stderr)
            return 1
        try:
            cost = estimate_rerun_cost(
                db, report, repetitions=args.repetitions, sides=args.sides,
                variant_ids=args.variant_ids,
            )  # fmt: skip
        except ComparisonError as exc:
            print(exc, file=sys.stderr)
            return 1
        chosen = args.variant_ids or default_rerun_variants(root_of(db, report))
        runs = args.repetitions * (2 if args.sides == "both" else 1)
        estimate = f"about ${cost:.2f}" if cost is not None else "unknown cost"
        print(f"Rerun {len(chosen)} variant(s) x {runs} run(s) ({args.sides}), {estimate}:")
        for variant_id in chosen:
            print(f"  {variant_id}")
        if (
            not args.yes
            and input("This calls the real Claude API. Continue? [y/N] ").lower() != "y"
        ):
            print("Cancelled.")
            return 1
        try:
            rerun = request_rerun(
                db, report, repetitions=args.repetitions, sides=args.sides,
                variant_ids=args.variant_ids,
            )  # fmt: skip
        except (ComparisonError, SelectionError) as exc:
            print(exc, file=sys.stderr)
            return 1
        rerun_id = rerun.id
    execute_rerun(rerun_id, session_factory=factory, llm=llm)
    with factory() as db:
        done = db.get(RegressionRerunRecord, rerun_id)
        assert done is not None
        if done.status != "completed" or done.result_report_id is None:
            print(f"Rerun {rerun_id} {done.status}: {done.error}", file=sys.stderr)
            return 1
        result_id = done.result_report_id
    return _report(result_id, as_json=False)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run scenarios against the real agent and grade them")
    run.add_argument("--scenario", action="append", default=[], dest="scenario_ids")
    run.add_argument("--variant", action="append", default=[], dest="variant_ids")
    run.add_argument("--tag", action="append", default=[], dest="tags")
    run.add_argument("--persona", action="append", default=[], dest="personas")
    run.add_argument("--agent-version", default=DEFAULT_AGENT_VERSION)
    run.add_argument("--judge", action="store_true", help="also run the LLM judge")
    run.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    show = sub.add_parser("show", help="print a stored run's metrics and failed checks")
    show.add_argument("run_id", type=int)
    sub.add_parser("versions", help="list agent versions")
    comp = sub.add_parser("compare", help="compare a candidate run with a baseline run")
    comp.add_argument("baseline_run_id", type=int)
    comp.add_argument("candidate_run_id", type=int)
    defaults = GateThresholds()
    comp.add_argument(
        "--max-task-success-drop",
        type=Decimal,
        default=defaults.max_task_success_drop_pp,
        help="percentage points",
    )
    comp.add_argument(
        "--max-cost-increase",
        type=Decimal,
        default=defaults.max_cost_increase_pct,
        help="percent (warning)",
    )
    comp.add_argument(
        "--max-latency-increase",
        type=Decimal,
        default=defaults.max_latency_increase_pct,
        help="percent (warning)",
    )
    comp.add_argument(
        "--warn-on-new-critical",
        action="store_true",
        help="report new critical failures as a warning instead of failing",
    )
    comp.add_argument("--json", action="store_true", help="print the report as JSON")
    report = sub.add_parser("report", help="print a stored comparison")
    report.add_argument("report_id", type=int)
    report.add_argument("--json", action="store_true")
    rerun = sub.add_parser("rerun", help="repeat flipped variants to tell noise from regressions")
    rerun.add_argument("report_id", type=int)
    rerun.add_argument("--repetitions", type=int, default=3)
    rerun.add_argument("--sides", choices=["both", "candidate"], default="both")
    rerun.add_argument("--variant", action="append", dest="variant_ids")
    rerun.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args(argv)

    if args.command == "show":
        return _show(args.run_id)
    if args.command == "versions":
        return _versions()
    if args.command == "compare":
        try:
            thresholds = GateThresholds(
                fail_on_new_critical=not args.warn_on_new_critical,
                max_task_success_drop_pp=args.max_task_success_drop,
                max_cost_increase_pct=args.max_cost_increase,
                max_latency_increase_pct=args.max_latency_increase,
            )
        except ValueError as exc:
            print(f"Invalid threshold: {exc}", file=sys.stderr)
            return 2
        return _compare(args.baseline_run_id, args.candidate_run_id, thresholds, args.json)
    if args.command == "report":
        return _report(args.report_id, args.json)

    api_key = get_settings().anthropic_api_key
    if api_key is None:
        print("ANTHROPIC_API_KEY is not set in backend/.env", file=sys.stderr)
        return 1
    if args.command == "rerun":
        return _rerun(args, ClaudeClient(api_key.get_secret_value()))
    try:
        get_version(args.agent_version)
    except UnknownAgentVersion as exc:
        print(exc, file=sys.stderr)
        return 1
    judge = ClaudeJudge(api_key.get_secret_value()) if args.judge else None
    selection = RunSelection(
        scenario_ids=args.scenario_ids,
        variant_ids=args.variant_ids,
        tags=args.tags,
        personas=args.personas,
    )

    try:
        cases = preview_cases(selection)
    except SelectionError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not cases:
        print("The selection matches no scenario variants.", file=sys.stderr)
        return 1
    print(f"{len(cases)} variant(s), agent {args.agent_version}, judge={'on' if judge else 'off'}:")
    for scenario, persona in cases:
        print(f"  {scenario.id}:{persona}")
    if not args.yes and input("This calls the real Claude API. Continue? [y/N] ").lower() != "y":
        print("Cancelled.")
        return 1

    factory = get_sessionmaker()
    with factory() as db:
        test_run = create_test_run(
            db,
            selection,
            agent_version=args.agent_version,
            judge_model=judge.model if judge else None,
        )
        run_id = test_run.id
    print(f"Test run {run_id} started")

    execute_test_run(
        run_id,
        session_factory=factory,
        llm=ClaudeClient(api_key.get_secret_value()),
        judge=judge,
        on_result=_print_result,
    )
    return _show(run_id)


if __name__ == "__main__":
    sys.exit(main())
