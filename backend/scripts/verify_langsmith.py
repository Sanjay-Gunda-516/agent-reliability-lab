"""Check the LangSmith setup without spending anything on Claude.

Runs the real agent graph and tools with a scripted Claude inside a rolled-back
transaction, then confirms the run reached LangSmith with our correlation metadata and
with content hidden. Finally shows that an unreachable LangSmith doesn't affect the agent.

    .venv/bin/python scripts/verify_langsmith.py
"""

import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langsmith import Client  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.agent.config import AgentConfig  # noqa: E402
from app.agent.service import run_agent  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.lab_models import ExecutionTraceRecord  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.observability import langsmith as ls  # noqa: E402
from app.observability.context import correlate, new_id  # noqa: E402
from app.observability.logging import configure_logging  # noqa: E402
from app.observability.trace_store import build_trace  # noqa: E402
from app.observability.tracing import TraceRecorder  # noqa: E402
from app.seed import ALICE, reset_demo_data  # noqa: E402
from tests.fakes import FakeLLM, calls, text, tool_use  # noqa: E402


def scripted_run(db: Session) -> tuple[str, TraceRecorder, Any]:
    llm = FakeLLM([calls(tool_use("get_order", {"order_id": 4821})), text("Processing.")])
    recorder = TraceRecorder()
    result = run_agent(
        db=db, llm=llm, customer_id=ALICE, message="Status of #4821?",
        config=AgentConfig(), recorder=recorder,
    )  # fmt: skip
    return result.execution_id, recorder, result


def find_root_run(client: Client, project: str, execution_id: str, timeout_s: float = 60) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for run in client.list_runs(project_name=project, is_root=True, limit=25):
            metadata = (run.extra or {}).get("metadata", {})
            if metadata.get("execution_id") == execution_id:
                return run
        time.sleep(3)
    return None


def main() -> int:
    configure_logging()
    settings = get_settings()
    config = ls.get_langsmith()
    if not config.enabled or config.client is None:
        print(f"LangSmith is off: {config.reason}")
        return 1
    if not settings.langsmith_hide_content:
        print("Refusing to run: set LANGSMITH_HIDE_CONTENT=true first.")
        return 1
    print(f"LangSmith on, project '{config.project}', content hidden")

    request_id = f"langsmith-check-{new_id()[:8]}"
    with get_engine().connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            reset_demo_data(db, datetime.now(UTC))
            db.commit()
            with correlate(request_id=request_id):
                execution_id, recorder, result = scripted_run(db)
                db.add(
                    build_trace(
                        execution_id=execution_id,
                        source="api",
                        customer_id=ALICE,
                        model="claude-opus-5",
                        status=result.status,
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                        turns=[],
                        messages=result.messages,
                        tool_calls=result.tool_calls,
                        events=recorder.events,
                    )  # fmt: skip
                )
                db.flush()
            ours = db.scalars(
                select(ExecutionTraceRecord).where(
                    ExecutionTraceRecord.execution_id == execution_id
                )
            ).one()
            print(
                f"Postgres trace: execution_id={execution_id} events={len(ours.events)} "
                f"tool_calls={len(ours.tool_calls)} request_id={ours.request_id}"
            )
        finally:
            db.close()
            transaction.rollback()  # nothing from this check is kept in the database

    config.client.flush()
    run = find_root_run(config.client, config.project, execution_id)
    if run is None:
        print("Run not found in LangSmith within 60s")
        return 1
    metadata = (run.extra or {}).get("metadata", {})
    children = list(config.client.list_runs(project_name=config.project, trace_id=run.trace_id))
    print(f"LangSmith run: name={run.name} status={run.status} child runs={len(children) - 1}")
    print(f"  child names: {sorted({c.name for c in children if c.id != run.id})}")
    print(
        "  metadata: "
        + ", ".join(
            f"{k}={metadata.get(k)}"
            for k in ("execution_id", "request_id", "model", "prompt_version")
        )
    )
    print(f"  inputs hidden: {not run.inputs}   outputs hidden: {not run.outputs}")
    same_ids = (metadata.get("execution_id"), metadata.get("request_id")) == (
        execution_id,
        request_id,
    )
    print(f"  matches Postgres: {same_ids}")

    # LangSmith unreachable: the agent must behave exactly the same.
    unreachable = Client(api_key="unused", api_url="http://127.0.0.1:9")
    ls.get_langsmith = lambda: ls.LangSmith(True, config.project, unreachable)  # type: ignore[assignment]
    with get_engine().connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            reset_demo_data(db, datetime.now(UTC))
            db.commit()
            started = time.perf_counter()
            _, _, result = scripted_run(db)
            took = (time.perf_counter() - started) * 1000
        finally:
            db.close()
            transaction.rollback()
    print(f"Unreachable LangSmith: status={result.status}, reply={result.final_response!r}")
    print(f"  agent run took {took:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
