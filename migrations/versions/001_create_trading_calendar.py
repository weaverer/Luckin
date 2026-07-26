"""创建交易日历当前值表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_calendar",
        sa.Column("market_code", sa.String(4, collation="ascii_bin"), nullable=False),
        sa.Column("calendar_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("previous_open_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_market", sa.String(32), nullable=False),
        sa.Column("sync_mode", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("market_code", "calendar_date"),
    )


def downgrade() -> None:
    op.drop_table("trading_calendar")
