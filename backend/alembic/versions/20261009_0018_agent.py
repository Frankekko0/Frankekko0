"""v3 phase 6: AI spend, agent runs, append-only event log

* ``ai_usage``   - one row per paid model call, so a daily and a monthly cap can stop spending;
* ``agent_runs`` - one row per run of the agent loop: the steps (trace), cost, validated result;
* ``events``     - append-only audit log. A trigger refuses UPDATE and DELETE, so what was done and
  why cannot be rewritten afterwards (the same guarantee ``analyses`` has).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("purpose", sa.String(48), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("tier", sa.String(8), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("ref", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage")),
    )
    op.create_index("ix_ai_usage_day", "ai_usage", ["day"])

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(24), nullable=False),
        sa.Column("started_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("input", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index("ix_agent_runs_started", "agent_runs", ["started_at"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(24), nullable=True),
        sa.Column("subject_id", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index("ix_events_kind_at", "events", ["kind", "at"])
    op.create_index("ix_events_subject", "events", ["subject_type", "subject_id"])
    op.execute(
        """
        CREATE FUNCTION events_are_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'events are append-only: % of event % refused', TG_OP, OLD.id;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER events_no_change BEFORE UPDATE OR DELETE ON events"
        " FOR EACH ROW EXECUTE FUNCTION events_are_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER events_no_change ON events")
    op.execute("DROP FUNCTION events_are_append_only()")
    op.drop_index("ix_events_subject", table_name="events")
    op.drop_index("ix_events_kind_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_agent_runs_started", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_ai_usage_day", table_name="ai_usage")
    op.drop_table("ai_usage")
