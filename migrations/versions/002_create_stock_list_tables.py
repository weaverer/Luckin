"""创建股票列表当前值、映射、同步结果和质量问题表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _utc_datetime(nullable: bool = False) -> sa.Column:
    return sa.Column(mysql.DATETIME(fsp=6), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "stock_current",
        sa.Column("stock_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("market_code", sa.String(4, collation="ascii_bin"), nullable=False),
        sa.Column("venue_code", sa.String(4, collation="ascii_bin"), nullable=False),
        sa.Column("security_code", sa.String(32, collation="ascii_bin"), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("currency_code", sa.String(3, collation="ascii_bin"), nullable=False),
        sa.Column("listing_status", sa.String(16, collation="ascii_bin"), nullable=False),
        sa.Column("listed_on", sa.Date(), nullable=True),
        sa.Column("delisted_on", sa.Date(), nullable=True),
        sa.Column("last_seen_run_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "market_code", "venue_code", "security_code", name="uq_stock_current_identity"
        ),
        sa.CheckConstraint("market_code = 'CN-S'", name="ck_stock_current_market"),
        sa.CheckConstraint(
            "venue_code IN ('XSHG','XSHE','XBSE')", name="ck_stock_current_venue"
        ),
        sa.CheckConstraint(
            "listing_status IN ('ACTIVE','DELISTED','SUSPENDED','PENDING')",
            name="ck_stock_current_status",
        ),
    )
    op.create_index(
        "ix_stock_current_filter",
        "stock_current",
        ["market_code", "listing_status", "venue_code", "security_code"],
    )
    op.create_index("ix_stock_current_display_name", "stock_current", ["display_name"])

    op.create_table(
        "stock_list_sync_run",
        sa.Column("run_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column("run_key", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("schedule_slug", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("scheduled_for", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("scope_code", sa.String(32, collation="ascii_bin"), nullable=False),
        sa.Column("scope_fingerprint", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("provider_code", sa.String(32, collation="ascii_bin"), nullable=False),
        sa.Column("flow_run_id", sa.String(64, collation="ascii_bin"), nullable=False),
        sa.Column("status", sa.String(12, collation="ascii_bin"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("segment_count", sa.SmallInteger(), nullable=False),
        sa.Column("completed_segment_count", sa.SmallInteger(), nullable=False),
        sa.Column("capped_segment_count", sa.SmallInteger(), nullable=False),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("baseline_count", sa.Integer(), nullable=True),
        sa.Column("added_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column("candidate_digest", sa.String(64, collation="ascii_bin"), nullable=True),
        sa.Column("error_category", sa.String(48, collation="ascii_bin"), nullable=True),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("run_key", name="uq_stock_list_sync_run_key"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_stock_list_sync_run_status",
        ),
    )

    op.create_table(
        "stock_provider_mapping",
        sa.Column("provider_code", sa.String(32, collation="ascii_bin"), primary_key=True),
        sa.Column(
            "provider_security_id",
            sa.String(96, collation="ascii_bin"),
            primary_key=True,
        ),
        sa.Column(
            "stock_id",
            sa.String(36, collation="ascii_bin"),
            sa.ForeignKey("stock_current.stock_id"),
            nullable=False,
        ),
        sa.Column("last_seen_run_id", sa.String(36, collation="ascii_bin"), nullable=False),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "provider_code", "stock_id", name="uq_stock_provider_mapping_stock"
        ),
    )

    op.create_table(
        "stock_list_sync_issue",
        sa.Column("issue_id", sa.String(36, collation="ascii_bin"), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36, collation="ascii_bin"),
            sa.ForeignKey("stock_list_sync_run.run_id"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32, collation="ascii_bin"), nullable=False),
        sa.Column(
            "provider_security_id_hash",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
        ),
        sa.Column("venue_code", sa.String(4, collation="ascii_bin"), nullable=True),
        sa.Column("security_code", sa.String(32, collation="ascii_bin"), nullable=True),
        sa.Column("field_name", sa.String(64, collation="ascii_bin"), nullable=True),
        sa.Column("safe_summary", sa.String(500), nullable=False),
        sa.Column("payload_hash", sa.String(64, collation="ascii_bin"), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
    )
    op.create_index(
        "ix_stock_list_sync_issue_run_attempt",
        "stock_list_sync_issue",
        ["run_id", "attempt_no"],
    )


def downgrade() -> None:
    op.drop_table("stock_list_sync_issue")
    op.drop_table("stock_provider_mapping")
    op.drop_table("stock_list_sync_run")
    op.drop_table("stock_current")

