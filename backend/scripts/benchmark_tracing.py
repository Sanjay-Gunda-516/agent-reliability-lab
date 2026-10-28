"""Measure what step-level tracing adds to one agent run.

Offline: Claude is scripted, the graph, tools and Postgres are real, and everything runs
inside a transaction that is rolled back. Run from backend/:

    .venv/bin/python scripts/benchmark_tracing.py
"""

import io
import logging
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.agent.config import AgentConfig  # noqa: E402
from app.agent.service import AgentRunResult, run_agent  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.observability.logging import JsonFormatter  # noqa: E402
from app.observability.trace_store import build_trace  # noqa: E402
from app.observability.tracing import EventType, TraceEvent, TraceRecorder  # noqa: E402
from app.seed import ALICE, reset_demo_data  # noqa: E402
from tests.fakes import FakeLLM, calls, text, tool_use  # noqa: E402

WARMUP, RUNS = 30, 300


class NullRecorder(TraceRecorder):
    """Same interface, records and logs nothing: the 'tracing off' baseline."""

    def _dummy(self, type_: EventType) -> TraceEvent:
        return TraceEvent(sequence=0, type=type_, started_at=datetime.now(UTC), turn=0)

    def point(self, type_: EventType, **_: Any) -> TraceEvent:
        return self._dummy(type_)

    @contextmanager
    def span(self, type_: EventType, **_: Any) -> Iterator[TraceEvent]:
        yield self._dummy(type_)


def one_run(db: Session, recorder: TraceRecorder) -> AgentRunResult:
    # Read-only (a lookup), so every iteration sees the same store.
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("It's processing.")])
    return run_agent(
        db=db, llm=llm, customer_id=ALICE, message="Status of #4821?",
        config=AgentConfig(), recorder=recorder,
    )  # fmt: skip


def time_ms(fn: Callable[[], Any]) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def main() -> None:
    # Trace logs are formatted as JSON into memory: the cost counts, the terminal stays quiet.
    sink = logging.StreamHandler(io.StringIO())
    sink.setFormatter(JsonFormatter())
    logging.getLogger().handlers = [sink]
    logging.getLogger().setLevel(logging.INFO)

    with get_engine().connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            reset_demo_data(db, datetime.now(UTC))
            db.commit()
            for _ in range(WARMUP):
                one_run(db, TraceRecorder())
                one_run(db, NullRecorder())

            traced, untraced, persist, events = [], [], [], []
            for _ in range(RUNS):  # interleaved so drift affects both equally
                recorder = TraceRecorder()
                traced.append(time_ms(lambda r=recorder: one_run(db, r)))
                untraced.append(time_ms(lambda: one_run(db, NullRecorder())))
                events.append(len(recorder.events))
                result = one_run(db, recorder := TraceRecorder())

                def save(
                    result: AgentRunResult = result, recorder: TraceRecorder = recorder
                ) -> None:
                    db.add(
                        build_trace(
                            execution_id=recorder.execution_id,
                            source="api",
                            customer_id=ALICE,
                            model="claude-opus-5",
                            status=result.status,
                            started_at=datetime.now(UTC),
                            finished_at=datetime.now(UTC),
                            turns=[{"message": "Status of #4821?"}],
                            messages=result.messages,
                            tool_calls=result.tool_calls,
                            events=recorder.events,
                        )  # fmt: skip
                    )
                    db.flush()

                persist.append(time_ms(save))
        finally:
            db.close()
            transaction.rollback()

    def stats(values: list[float]) -> str:
        p95 = statistics.quantiles(values, n=20)[-1]
        return f"median {statistics.median(values):6.3f} ms   p95 {p95:6.3f} ms"

    overhead = statistics.median(traced) - statistics.median(untraced)
    print(
        f"{RUNS} runs each (after {WARMUP} warm-up), {statistics.median(events):.0f} events per run"
    )
    print(f"agent run, tracing off : {stats(untraced)}")
    print(f"agent run, tracing on  : {stats(traced)}")
    print(f"recording overhead     : {overhead:+.3f} ms per run "
          f"({overhead / statistics.median(untraced):+.1%} of an offline run)")  # fmt: skip
    print(f"saving the trace rows  : {stats(persist)}")


if __name__ == "__main__":
    main()
