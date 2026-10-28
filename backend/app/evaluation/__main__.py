"""Run and inspect evaluations from the command line. Run from backend/:

    python -m app.evaluation run --scenario cancel-eligible-order --persona normal --judge
    python -m app.evaluation show 12

`run` calls the real Claude API (it costs money) and asks before starting.
"""

import argparse
import json
import sys

from sqlalchemy import select

from app.agent.config import AgentConfig
from app.agent.llm import ClaudeClient
from app.config import get_settings
from app.db.lab_models import EvaluationCheckRecord, EvaluationResultRecord, TestRunRecord
from app.db.session import get_sessionmaker
from app.evaluation.evaluate import EvaluatedVariant
from app.evaluation.judge import ClaudeJudge
from app.evaluation.metrics import compute_metrics
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


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run scenarios against the real agent and grade them")
    run.add_argument("--scenario", action="append", default=[], dest="scenario_ids")
    run.add_argument("--variant", action="append", default=[], dest="variant_ids")
    run.add_argument("--tag", action="append", default=[], dest="tags")
    run.add_argument("--persona", action="append", default=[], dest="personas")
    run.add_argument("--judge", action="store_true", help="also run the LLM judge")
    run.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    show = sub.add_parser("show", help="print a stored run's metrics and failed checks")
    show.add_argument("run_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "show":
        return _show(args.run_id)

    api_key = get_settings().anthropic_api_key
    if api_key is None:
        print("ANTHROPIC_API_KEY is not set in backend/.env", file=sys.stderr)
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
    print(f"{len(cases)} variant(s), judge={'on' if judge else 'off'}:")
    for scenario, persona in cases:
        print(f"  {scenario.id}:{persona}")
    if not args.yes and input("This calls the real Claude API. Continue? [y/N] ").lower() != "y":
        print("Cancelled.")
        return 1

    factory = get_sessionmaker()
    with factory() as db:
        test_run = create_test_run(
            db, selection, config=AgentConfig(), judge_model=judge.model if judge else None
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
