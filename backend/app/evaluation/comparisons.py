"""Storing regression comparisons, rerunning flipped variants, and rendering reports.

A report is written once and never edited. A rerun repeats chosen variants k times as
new test runs (linked by `test_runs.rerun_id`), then writes a new revision of the
original report. Each revision uses every rerun observation collected so far for that
comparison, and keeps the evidence behind each reclassification.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.config import AgentConfig
from app.agent.llm import LLMClient
from app.db.lab_models import (
    RegressionReportRecord,
    RegressionRerunRecord,
    RegressionVariantRecord,
    TestRunRecord,
)
from app.evaluation.metrics import VariantSummary
from app.evaluation.regression import (
    RERUNNABLE,
    Category,
    Comparison,
    GateThresholds,
    RerunEvidence,
    assess_reruns,
    compare,
)
from app.evaluation.service import RunSelection, create_test_run, execute_test_run
from app.evaluation.store import load_summaries
from app.observability.context import correlate, current

logger = logging.getLogger(__name__)

Sides = Literal["candidate", "both"]
MAX_REPETITIONS = 5


class ComparisonError(Exception):
    def __init__(self, kind: Literal["not_found", "not_ready", "incompatible"], message: str):
        super().__init__(message)
        self.kind = kind


def _completed_run(db: Session, run_id: int, role: str) -> TestRunRecord:
    run = db.get(TestRunRecord, run_id)
    if run is None:
        raise ComparisonError("not_found", f"{role} test run {run_id} not found")
    if run.status != "completed":
        raise ComparisonError(
            "not_ready", f"{role} test run {run_id} is {run.status}, not completed"
        )
    return run


def create_comparison(
    db: Session,
    baseline_run_id: int,
    candidate_run_id: int,
    thresholds: GateThresholds | None = None,
) -> RegressionReportRecord:
    if baseline_run_id == candidate_run_id:
        raise ComparisonError("incompatible", "baseline and candidate are the same run")
    baseline = _completed_run(db, baseline_run_id, "baseline")
    candidate = _completed_run(db, candidate_run_id, "candidate")
    base, cand = load_summaries(db, baseline.id), load_summaries(db, candidate.id)
    if not {v.variant_id for v in base} & {v.variant_id for v in cand}:
        raise ComparisonError(
            "incompatible", "the runs have no scenario variants in common, so there's nothing "
            "to compare"
        )  # fmt: skip
    thresholds = thresholds or GateThresholds()
    report = _save(db, compare(base, cand, thresholds), baseline, candidate, thresholds)
    db.commit()
    return report


def _save(
    db: Session,
    comparison: Comparison,
    baseline: TestRunRecord,
    candidate: TestRunRecord,
    thresholds: GateThresholds,
    *,
    root: RegressionReportRecord | None = None,
    rerun: RegressionRerunRecord | None = None,
) -> RegressionReportRecord:
    revision = 1
    if root is not None:
        revision = 1 + (
            db.scalar(
                select(func.max(RegressionReportRecord.revision)).where(
                    (RegressionReportRecord.root_report_id == root.id)
                    | (RegressionReportRecord.id == root.id)
                )
            )
            or 1
        )
    report = RegressionReportRecord(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        baseline_agent_version=baseline.agent_version,
        candidate_agent_version=candidate.agent_version,
        revision=revision,
        root_report_id=root.id if root else None,
        rerun_id=rerun.id if rerun else None,
        thresholds=thresholds.model_dump(mode="json"),
        gate_status=comparison.gate.status,
        gate=comparison.gate.model_dump(mode="json"),
        counts=comparison.counts,
        metric_deltas=[d.model_dump(mode="json") for d in comparison.metric_deltas],
        baseline_metrics=comparison.baseline_metrics.model_dump(mode="json"),
        candidate_metrics=comparison.candidate_metrics.model_dump(mode="json"),
        request_id=current().request_id,
        variants=[
            RegressionVariantRecord(
                **v.model_dump(
                    mode="json",
                    exclude={"rerun_evidence", "cost_delta_usd", "latency_delta_ms"},
                ),
                latency_delta_ms=v.latency_delta_ms,
                cost_delta_usd=v.cost_delta_usd,
                rerun_evidence=v.rerun_evidence.model_dump(mode="json")
                if v.rerun_evidence
                else None,
            )
            for v in comparison.variants
        ],
    )
    db.add(report)
    db.flush()
    return report


def root_of(db: Session, report: RegressionReportRecord) -> RegressionReportRecord:
    if report.root_report_id is None:
        return report
    root = db.get(RegressionReportRecord, report.root_report_id)
    assert root is not None
    return root


def revisions(db: Session, report: RegressionReportRecord) -> list[RegressionReportRecord]:
    root = root_of(db, report)
    return list(
        db.scalars(
            select(RegressionReportRecord)
            .where(
                (RegressionReportRecord.id == root.id)
                | (RegressionReportRecord.root_report_id == root.id)
            )
            .order_by(RegressionReportRecord.revision)
        )
    )


def default_rerun_variants(report: RegressionReportRecord) -> list[str]:
    return [v.variant_id for v in report.variants if v.needs_rerun]


def _rerun_targets(
    db: Session, report: RegressionReportRecord, variant_ids: list[str] | None
) -> tuple[RegressionReportRecord, list[str]]:
    root = root_of(db, report)
    first = {v.variant_id: v for v in root.variants}
    chosen = variant_ids or default_rerun_variants(root)
    if not chosen:
        raise ComparisonError("incompatible", "no variant needs a rerun in this comparison")
    unknown = sorted(set(chosen) - first.keys())
    if unknown:
        raise ComparisonError("incompatible", f"not in this comparison: {unknown}")
    not_rerunnable = sorted(v for v in chosen if Category(first[v].category) not in RERUNNABLE)
    if not_rerunnable:
        raise ComparisonError(
            "incompatible",
            f"only paired variants with unchanged scenarios can be rerun: {not_rerunnable}",
        )
    return root, sorted(set(chosen))


def estimate_rerun_cost(
    db: Session,
    report: RegressionReportRecord,
    *,
    repetitions: int,
    sides: Sides,
    variant_ids: list[str] | None = None,
) -> Decimal | None:
    """From what the chosen variants cost in the original runs; None if unknown."""
    root, chosen = _rerun_targets(db, report, variant_ids)
    runs = [root.candidate_run_id] + ([root.baseline_run_id] if sides == "both" else [])
    total = Decimal(0)
    for run_id in runs:
        costs = {s.variant_id: s.cost_usd for s in load_summaries(db, run_id)}
        for variant_id in chosen:
            cost = costs.get(variant_id)
            if cost is None:
                return None
            total += cost * repetitions
    return total


def request_rerun(
    db: Session,
    report: RegressionReportRecord,
    *,
    repetitions: int = 3,
    sides: Sides = "both",
    variant_ids: list[str] | None = None,
) -> RegressionRerunRecord:
    """Queue the rerun's test runs: `repetitions` per side, each with the chosen variants.

    Each side repeats exactly the config its original run used.
    """
    if not 1 <= repetitions <= MAX_REPETITIONS:
        raise ComparisonError("incompatible", f"repetitions must be 1–{MAX_REPETITIONS}")
    root, chosen = _rerun_targets(db, report, variant_ids)
    rerun = RegressionRerunRecord(
        report_id=root.id,
        repetitions=repetitions,
        sides=sides,
        variant_ids=chosen,
        status="queued",
        request_id=current().request_id,
    )
    db.add(rerun)
    db.flush()
    side_runs = [("candidate", root.candidate_run_id)]
    if sides == "both":
        side_runs.append(("baseline", root.baseline_run_id))
    for side, original_id in side_runs:
        original = db.get(TestRunRecord, original_id)
        assert original is not None
        for repetition in range(1, repetitions + 1):
            create_test_run(
                db,
                RunSelection(variant_ids=chosen),
                agent_version=original.agent_version,
                config=AgentConfig(**original.agent_config),
                judge_model=None,  # the gate never uses the judge
                rerun_id=rerun.id,
                rerun_side=side,
                repetition=repetition,
            )
    db.commit()
    return rerun


def rerun_runs(db: Session, rerun_id: int) -> list[TestRunRecord]:
    return list(
        db.scalars(
            select(TestRunRecord)
            .where(TestRunRecord.rerun_id == rerun_id)
            .order_by(TestRunRecord.id)
        )
    )


def execute_rerun(rerun_id: int, *, session_factory: sessionmaker[Session], llm: LLMClient) -> None:
    with correlate(), session_factory() as db:
        rerun = db.get(RegressionRerunRecord, rerun_id)
        if rerun is None:
            raise ValueError(f"rerun {rerun_id} not found")
        rerun.status = "running"
        db.commit()
        run_ids = [r.id for r in rerun_runs(db, rerun_id)]
    try:
        for run_id in run_ids:
            execute_test_run(run_id, session_factory=session_factory, llm=llm, judge=None)
        with session_factory() as db:
            failed = [r.id for r in rerun_runs(db, rerun_id) if r.status != "completed"]
            if failed:
                raise RuntimeError(f"rerun test run(s) {failed} did not complete")
            rerun = db.get(RegressionRerunRecord, rerun_id)
            assert rerun is not None
            revision = build_revision(db, rerun)
            rerun.status = "completed"
            rerun.result_report_id = revision.id
            rerun.finished_at = datetime.now(UTC)
            db.commit()
    except Exception as exc:
        logger.exception("Regression rerun %s failed", rerun_id)
        with session_factory() as db:
            rerun = db.get(RegressionRerunRecord, rerun_id)
            assert rerun is not None
            rerun.status = "failed"
            rerun.error = f"{type(exc).__name__}: {exc}"
            rerun.finished_at = datetime.now(UTC)
            db.commit()


def build_revision(db: Session, rerun: RegressionRerunRecord) -> RegressionReportRecord:
    """A new revision of the original comparison, using all completed rerun observations."""
    root = db.get(RegressionReportRecord, rerun.report_id)
    assert root is not None
    baseline = db.get(TestRunRecord, root.baseline_run_id)
    candidate = db.get(TestRunRecord, root.candidate_run_id)
    assert baseline is not None and candidate is not None
    reruns = db.scalars(
        select(RegressionRerunRecord).where(
            RegressionRerunRecord.report_id == root.id,
            (RegressionRerunRecord.status == "completed") | (RegressionRerunRecord.id == rerun.id),
        )
    )
    observations: dict[tuple[str, str], list[VariantSummary]] = defaultdict(list)
    for previous in reruns:
        for run in rerun_runs(db, previous.id):
            for summary in load_summaries(db, run.id):
                observations[(str(run.rerun_side), summary.variant_id)].append(summary)

    base = load_summaries(db, baseline.id)
    cand = load_summaries(db, candidate.id)
    base_by_id = {s.variant_id: s for s in base}
    cand_by_id = {s.variant_id: s for s in cand}
    evidence: dict[str, RerunEvidence] = {}
    for variant_id in sorted({vid for _, vid in observations}):
        if variant_id in base_by_id and variant_id in cand_by_id:
            evidence[variant_id] = assess_reruns(
                base_by_id[variant_id],
                cand_by_id[variant_id],
                candidate_reruns=observations[("candidate", variant_id)],
                baseline_reruns=observations[("baseline", variant_id)],
            )
    thresholds = GateThresholds.model_validate(root.thresholds)
    revision = _save(
        db, compare(base, cand, thresholds, evidence), baseline, candidate, thresholds,
        root=root, rerun=rerun,
    )  # fmt: skip
    return revision


def render_report(report: RegressionReportRecord) -> str:
    """A plain-text summary for terminals and CI logs."""
    gate = report.gate
    lines = [
        f"Regression report {report.id} (revision {report.revision}): "
        f"{report.candidate_agent_version} run {report.candidate_run_id} vs "
        f"{report.baseline_agent_version} run {report.baseline_run_id}",
        f"GATE: {report.gate_status.upper()}",
    ]
    for reason in gate["reasons"]:
        lines.append(f"  [{reason['level'].upper()}] {reason['code']}: {reason['message']}")
    counts = ", ".join(f"{k} {n}" for k, n in report.counts.items() if n)
    lines += ["", f"Variants: {counts}"]
    for variant in report.variants:
        if variant.category != Category.UNCHANGED:
            lines.append(f"  {variant.category:<21} {variant.variant_id}: {variant.explanation}")
    lines += ["", "Metrics (gated variants):"]
    for delta in report.metric_deltas:
        if delta["baseline"] is None and delta["candidate"] is None:
            continue
        if delta["unit"] == "pp":
            lines.append(
                f"  {delta['name']:<28} {delta['baseline_detail']:>7} → "
                f"{delta['candidate_detail']:<7} ({_signed(delta['delta'])} pp)"
            )
        else:
            change = f", {_signed(delta['change_pct'])}%" if delta["change_pct"] is not None else ""
            lines.append(
                f"  {delta['name']:<28} {_num(delta['baseline'])} → {_num(delta['candidate'])}"
                f" ({_signed(delta['delta'])}{change})"
            )
    return "\n".join(lines)


def _signed(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+g}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"
