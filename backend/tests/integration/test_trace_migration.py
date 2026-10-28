"""The Phase 6 migration must carry existing evaluation traces over intact."""

import json
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.session import get_engine
from app.scenarios.loader import load_scenarios

pytestmark = pytest.mark.integration

BEFORE_TRACING = "f70ac0d71467"

LEGACY_TURNS = [
    {
        "message": "Cancel #4821",
        "status": "completed",
        "final_response": "Done.",
        "escalation_reason": None,
        "intent": {"intent": "cancel_order", "order_ids": [4821]},
        "latency_ms": 900.0,
        "llm_calls": [],
        "tool_calls": [
            {"tool_use_id": "t1", "name": "get_order", "input": {"order_id": 4821},
             "status": "success", "output": {"ok": True}, "error": None, "attempts": 1,
             "latency_ms": 4.0},
            {"tool_use_id": "t2", "name": "cancel_order", "input": {"order_id": 4821},
             "status": "success", "output": {"ok": True}, "error": None, "attempts": 3,
             "latency_ms": 30.0},
        ],
    }
]  # fmt: skip


@pytest.fixture
def at_old_schema(alembic_config: Config) -> Iterator[None]:
    command.downgrade(alembic_config, BEFORE_TRACING)
    try:
        yield
    finally:
        command.upgrade(alembic_config, "head")


def test_upgrade_backfills_legacy_traces(at_old_schema: None, alembic_config: Config) -> None:
    scenario = next(s for s in load_scenarios() if s.id == "cancel-eligible-order")
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM execution_traces"))
        conn.execute(text("DELETE FROM evaluation_checks"))
        conn.execute(text("DELETE FROM evaluation_results"))
        conn.execute(
            text(
                "INSERT INTO test_scenarios (id, title, tags, customer_id, expected_outcome,"
                " expected_status, definition, content_hash) VALUES (:id, 't', ARRAY['happy-path'],"
                " 1001, 'order_cancelled', 'completed', '{}', 'h') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": scenario.id},
        )
        conn.execute(
            text(
                "INSERT INTO scenario_variants (id, scenario_id, persona, turns) VALUES"
                " (:vid, :sid, 'normal', '[]') ON CONFLICT (id) DO NOTHING"
            ),
            {"vid": f"{scenario.id}:normal", "sid": scenario.id},
        )
        run_id = conn.execute(
            text(
                "INSERT INTO test_runs (status, agent_version, agent_config, selection,"
                " variant_count) VALUES ('completed', 'v-legacy', '{\"model\": \"claude-opus-5\"}',"
                " '{}', 1) RETURNING id"
            )
        ).scalar_one()
        result_id = conn.execute(
            text(
                "INSERT INTO evaluation_results (test_run_id, scenario_id, variant_id, persona,"
                " scenario_hash, verdict, critical_failures, major_failures, minor_failures,"
                " final_status, models, latency_ms, llm_calls, input_tokens, output_tokens,"
                " cache_creation_input_tokens, cache_read_input_tokens, tool_calls, tool_errors,"
                " started_at) VALUES (:run, :sid, :vid, 'normal', 'h', 'pass', 0, 0, 0,"
                " 'completed', ARRAY['claude-opus-5'], 900, 3, 10, 5, 0, 0, 2, 0, now())"
                " RETURNING id"
            ),
            {"run": run_id, "sid": scenario.id, "vid": f"{scenario.id}:normal"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO execution_traces (evaluation_result_id, turns, messages,"
                " injected_faults) VALUES (:rid, :turns, '[]', '[]')"
            ),
            {"rid": result_id, "turns": json.dumps(LEGACY_TURNS)},
        )

    command.upgrade(alembic_config, "head")

    with get_engine().connect() as conn:
        trace = conn.execute(
            text(
                "SELECT t.id, t.execution_id, t.source, t.status, t.model, t.agent_version,"
                " t.customer_id, t.test_run_id FROM execution_traces t"
                " JOIN evaluation_results r ON r.execution_trace_id = t.id WHERE r.id = :rid"
            ),
            {"rid": result_id},
        ).one()
        tools = conn.execute(
            text(
                "SELECT sequence, turn, name, arguments, attempts, started_at FROM tool_calls"
                " WHERE execution_trace_id = :tid ORDER BY sequence"
            ),
            {"tid": trace.id},
        ).all()

    assert len(trace.execution_id) == 32
    assert (trace.source, trace.status, trace.model) == ("evaluation", "completed", "claude-opus-5")
    assert (trace.agent_version, trace.customer_id, trace.test_run_id) == ("v-legacy", 1001, run_id)
    assert [(t.sequence, t.turn, t.name, t.attempts) for t in tools] == [
        (1, 1, "get_order", 1),
        (2, 1, "cancel_order", 3),
    ]
    assert tools[0].arguments == {"order_id": 4821}
    assert tools[0].started_at is None  # not recorded before tracing existed
