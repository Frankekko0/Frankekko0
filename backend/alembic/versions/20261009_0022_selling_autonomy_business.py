"""v3 phases 8, 8b, 8d: selling cycle, learning from outcomes, autonomy limits, business goals

* ``inventory`` gets the stages of the selling cycle (to list, listed, sold...), the price history with
  its markdowns, the floor price and the numbers needed to reprice.
* ``prediction_outcomes``: for every closed sale, what was forecast against what happened.
* ``autonomy_settings`` and ``autonomy_actions``: the user's hard limits, the kill switch, the dry-run
  period and what the system decided to do (or would have done). The audit trail itself is ``events``.
* ``business_goals``, ``expenses``: goals, tax thresholds the user configures, and costs that are not
  purchases (materials, packaging, labour).
* ``experiments``: the registry of hypotheses and their results (nothing is tried twice).

Additive and reversible.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.add_column("inventory", sa.Column("stage", sa.String(16), nullable=False, server_default="to_list"))
    op.add_column("inventory", sa.Column("received_at", sa.DateTime(timezone=True)))
    op.add_column("inventory", sa.Column("min_price", MONEY))
    op.add_column("inventory", sa.Column("initial_price", MONEY))
    op.add_column(
        "inventory",
        sa.Column("price_history", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("inventory", sa.Column("views", sa.Integer()))
    op.add_column("inventory", sa.Column("favourites", sa.Integer()))
    op.add_column("inventory", sa.Column("listing_url", sa.Text()))
    op.execute("UPDATE inventory SET stage = 'returned' WHERE status = 'returned'")
    op.create_index("ix_inventory_user_stage", "inventory", ["user_id", "stage"])

    op.create_table(
        "prediction_outcomes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_id", UUID, sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="SET NULL")),
        sa.Column("brand_name", sa.String(120)),
        sa.Column("category_name", sa.String(120)),
        sa.Column("price_band", sa.String(12)),
        sa.Column("verdict_at_buy", sa.String(24)),
        sa.Column("predicted_price", MONEY),
        sa.Column("predicted_days", sa.Numeric(8, 2)),
        sa.Column("predicted_profit", MONEY),
        sa.Column("predicted_p_sale", sa.Numeric(6, 4)),
        sa.Column("actual_price", MONEY, nullable=False),
        sa.Column("actual_days", sa.Integer(), nullable=False),
        sa.Column("actual_profit", MONEY, nullable=False),
        sa.Column("price_error_pct", sa.Numeric(8, 4)),
        sa.Column("days_error", sa.Numeric(8, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sale_id", name="uq_prediction_outcomes_sale_id"),
    )
    op.create_index("ix_prediction_outcomes_user", "prediction_outcomes", ["user_id", "created_at"])

    op.create_table(
        "autonomy_settings",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", sa.String(12), nullable=False, server_default="dry_run"),
        sa.Column("dry_run_until", sa.DateTime(timezone=True)),
        sa.Column("killed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suspended_reason", sa.Text()),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
        sa.Column("limits", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "autonomy_actions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("opportunity_id", UUID, sa.ForeignKey("opportunities.id", ondelete="SET NULL")),
        sa.Column("inventory_id", UUID, sa.ForeignKey("inventory.id", ondelete="SET NULL")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("verifier", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_autonomy_actions_user", "autonomy_actions", ["user_id", "created_at"])

    op.create_table(
        "business_goals",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("monthly_profit_target", MONEY),
        sa.Column("initial_capital", MONEY),
        sa.Column("max_capital", MONEY),
        sa.Column("weekly_hours", sa.SmallInteger()),
        sa.Column("horizon_months", sa.SmallInteger(), nullable=False, server_default="12"),
        sa.Column("reinvest_pct", sa.Numeric(5, 4), nullable=False, server_default="0.7"),
        sa.Column("min_reserve", MONEY, nullable=False, server_default="0"),
        sa.Column("explore_share", sa.Numeric(5, 4), nullable=False, server_default="0.10"),
        sa.Column("holder_status", sa.String(16), nullable=False, server_default="private"),
        sa.Column(
            "tax_thresholds", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "expenses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("purchase_id", UUID, sa.ForeignKey("purchases.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_expenses_amount_non_negative"),
    )
    op.create_index("ix_expenses_user_date", "expenses", ["user_id", "spent_on"])

    op.create_table(
        "experiments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_experiments_fingerprint", "experiments", ["fingerprint"])


def downgrade() -> None:
    op.drop_table("experiments")
    op.drop_table("expenses")
    op.drop_table("business_goals")
    op.drop_table("autonomy_actions")
    op.drop_table("autonomy_settings")
    op.drop_table("prediction_outcomes")
    op.drop_index("ix_inventory_user_stage", table_name="inventory")
    for col in (
        "listing_url",
        "favourites",
        "views",
        "price_history",
        "initial_price",
        "min_price",
        "received_at",
        "stage",
    ):
        op.drop_column("inventory", col)
