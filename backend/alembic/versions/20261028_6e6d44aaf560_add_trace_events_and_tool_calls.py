"""add trace events and tool calls

Revision ID: 6e6d44aaf560
Revises: f70ac0d71467
Create Date: 2026-10-28 14:28:54.837713

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "6e6d44aaf560"
down_revision: str | Sequence[str] | None = "f70ac0d71467"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make execution_traces the root of every agent execution and add events/tool calls.

    Existing evaluation traces are migrated in place: they get an execution id and their
    tool calls (previously only inside the turns JSON) are copied into tool_calls.
    """
    # --- execution_traces: new identity and correlation columns (filled below) --------
    op.add_column("execution_traces", sa.Column("execution_id", sa.String(32), nullable=True))
    op.add_column("execution_traces", sa.Column("source", sa.String(20), nullable=True))
    op.add_column("execution_traces", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("execution_traces", sa.Column("test_run_id", sa.Integer(), nullable=True))
    op.add_column("execution_traces", sa.Column("scenario_id", sa.String(80), nullable=True))
    op.add_column("execution_traces", sa.Column("variant_id", sa.String(120), nullable=True))
    op.add_column("execution_traces", sa.Column("customer_id", sa.Integer(), nullable=True))
    op.add_column("execution_traces", sa.Column("agent_version", sa.String(50), nullable=True))
    op.add_column("execution_traces", sa.Column("model", sa.String(50), nullable=True))
    op.add_column("execution_traces", sa.Column("status", sa.String(20), nullable=True))
    op.add_column("execution_traces", sa.Column("escalation_reason", sa.String(40), nullable=True))
    op.add_column("execution_traces", sa.Column("error", sa.Text(), nullable=True))
    op.add_column(
        "execution_traces", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "execution_traces", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("execution_traces", "injected_faults", nullable=True)
    op.add_column("test_runs", sa.Column("request_id", sa.String(64), nullable=True))

    op.execute(
        """
        UPDATE execution_traces AS t SET
            execution_id = replace(gen_random_uuid()::text, '-', ''),
            source = 'evaluation',
            test_run_id = r.test_run_id,
            scenario_id = r.scenario_id,
            variant_id = r.variant_id,
            customer_id = s.customer_id,
            agent_version = run.agent_version,
            model = coalesce(r.models[1], run.agent_config->>'model'),
            status = coalesce(r.final_status, 'error'),
            escalation_reason = r.escalation_reason,
            error = r.error,
            started_at = r.started_at
        FROM evaluation_results AS r
        JOIN test_scenarios AS s ON s.id = r.scenario_id
        JOIN test_runs AS run ON run.id = r.test_run_id
        WHERE t.evaluation_result_id = r.id
        """
    )
    for column in ("execution_id", "source", "customer_id", "model", "status", "started_at"):
        op.alter_column("execution_traces", column, nullable=False)
    op.create_unique_constraint(
        op.f("uq_execution_traces_execution_id"), "execution_traces", ["execution_id"]
    )
    op.create_index(
        op.f("ix_execution_traces_request_id"), "execution_traces", ["request_id"], unique=False
    )
    op.create_index(
        op.f("ix_execution_traces_test_run_id"), "execution_traces", ["test_run_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_execution_traces_test_run_id_test_runs"),
        "execution_traces",
        "test_runs",
        ["test_run_id"],
        ["id"],
    )

    # --- results point to their trace (a trace can exist without a result) ------------
    op.add_column(
        "evaluation_results", sa.Column("execution_trace_id", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE evaluation_results AS r SET execution_trace_id = t.id
        FROM execution_traces AS t WHERE t.evaluation_result_id = r.id
        """
    )
    op.alter_column("evaluation_results", "execution_trace_id", nullable=False)
    op.create_unique_constraint(
        op.f("uq_evaluation_results_execution_trace_id"),
        "evaluation_results",
        ["execution_trace_id"],
    )
    op.create_foreign_key(
        op.f("fk_evaluation_results_execution_trace_id_execution_traces"),
        "evaluation_results",
        "execution_traces",
        ["execution_trace_id"],
        ["id"],
    )
    op.drop_constraint(
        "fk_execution_traces_evaluation_result_id_evaluation_results",
        "execution_traces",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_execution_traces_evaluation_result_id", "execution_traces", type_="unique"
    )
    op.drop_column("execution_traces", "evaluation_result_id")

    # --- trace events -----------------------------------------------------------------
    op.create_table(
        "trace_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_trace_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("node", sa.String(30), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("tool_name", sa.String(50), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_trace_id"],
            ["execution_traces.id"],
            name=op.f("fk_trace_events_execution_trace_id_execution_traces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_events")),
        sa.UniqueConstraint(
            "execution_trace_id", "sequence", name=op.f("uq_trace_events_execution_trace_id")
        ),
    )
    op.create_index(
        op.f("ix_trace_events_execution_trace_id"), "trace_events", ["execution_trace_id"]
    )
    op.create_index(op.f("ix_trace_events_type"), "trace_events", ["type"])

    # --- tool calls -------------------------------------------------------------------
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("execution_trace_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("tool_use_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.String(50), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["execution_trace_id"],
            ["execution_traces.id"],
            name=op.f("fk_tool_calls_execution_trace_id_execution_traces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    op.create_index(op.f("ix_tool_calls_execution_trace_id"), "tool_calls", ["execution_trace_id"])
    op.create_index(op.f("ix_tool_calls_name"), "tool_calls", ["name"])
    op.create_index(op.f("ix_tool_calls_status"), "tool_calls", ["status"])

    # Copy tool calls recorded before this migration out of the turns JSON.
    op.execute(
        """
        INSERT INTO tool_calls (
            execution_trace_id, sequence, turn, tool_use_id, name, arguments, status,
            error, attempts, latency_ms, output
        )
        SELECT
            t.id,
            row_number() OVER (PARTITION BY t.id ORDER BY turn.idx, call.idx),
            turn.idx,
            call.value->>'tool_use_id',
            call.value->>'name',
            coalesce(call.value->'input', '{}'::jsonb),
            call.value->>'status',
            call.value->>'error',
            coalesce((call.value->>'attempts')::int, 0),
            coalesce((call.value->>'latency_ms')::float, 0),
            nullif(call.value->'output', 'null'::jsonb)
        FROM execution_traces AS t
        CROSS JOIN LATERAL jsonb_array_elements(t.turns) WITH ORDINALITY AS turn(value, idx)
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(turn.value->'tool_calls', '[]'::jsonb))
            WITH ORDINALITY AS call(value, idx)
        """
    )


def downgrade() -> None:
    """Back to evaluation-owned traces. Traces of direct API chats have no result to hang
    off and are deleted, as are events and normalized tool calls."""
    op.drop_table("tool_calls")
    op.drop_table("trace_events")

    op.add_column(
        "execution_traces", sa.Column("evaluation_result_id", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE execution_traces AS t SET evaluation_result_id = r.id
        FROM evaluation_results AS r WHERE r.execution_trace_id = t.id
        """
    )
    op.drop_constraint(
        op.f("fk_evaluation_results_execution_trace_id_execution_traces"),
        "evaluation_results",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_evaluation_results_execution_trace_id"), "evaluation_results", type_="unique"
    )
    op.drop_column("evaluation_results", "execution_trace_id")
    op.execute("DELETE FROM execution_traces WHERE evaluation_result_id IS NULL")
    op.alter_column("execution_traces", "evaluation_result_id", nullable=False)
    op.create_unique_constraint(
        "uq_execution_traces_evaluation_result_id", "execution_traces", ["evaluation_result_id"]
    )
    op.create_foreign_key(
        "fk_execution_traces_evaluation_result_id_evaluation_results",
        "execution_traces",
        "evaluation_results",
        ["evaluation_result_id"],
        ["id"],
    )

    op.drop_constraint(
        op.f("fk_execution_traces_test_run_id_test_runs"), "execution_traces", type_="foreignkey"
    )
    op.drop_index(op.f("ix_execution_traces_test_run_id"), table_name="execution_traces")
    op.drop_index(op.f("ix_execution_traces_request_id"), table_name="execution_traces")
    op.drop_constraint(op.f("uq_execution_traces_execution_id"), "execution_traces", type_="unique")
    op.execute(
        "UPDATE execution_traces SET injected_faults = '[]'::jsonb WHERE injected_faults IS NULL"
    )
    op.alter_column("execution_traces", "injected_faults", nullable=False)
    for column in (
        "finished_at", "started_at", "error", "escalation_reason", "status", "model",
        "agent_version", "customer_id", "variant_id", "scenario_id", "test_run_id",
        "request_id", "source", "execution_id",
    ):  # fmt: skip
        op.drop_column("execution_traces", column)
    op.drop_column("test_runs", "request_id")
