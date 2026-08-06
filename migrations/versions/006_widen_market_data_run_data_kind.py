"""加宽 market_data_sync_run.data_kind（008 股东 TOP10_FLOAT_HOLDERS 18 字符超出原 16）。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "market_data_sync_run",
        "data_kind",
        existing_type=sa.String(16, collation="ascii_bin"),
        type_=sa.String(32, collation="ascii_bin"),
        existing_nullable=False,
        comment="数据类：DAILY_QUOTE、ADJ_FACTOR、DAILY_BASIC、WEEKLY_KLINE、MONTHLY_KLINE、"
        "INDEX_FACTOR、STOCK_FACTOR、TOP10_HOLDERS、TOP10_FLOAT_HOLDERS或HOLDER_COUNT",
    )


def downgrade() -> None:
    op.alter_column(
        "market_data_sync_run",
        "data_kind",
        existing_type=sa.String(32, collation="ascii_bin"),
        type_=sa.String(16, collation="ascii_bin"),
        existing_nullable=False,
        comment="数据类：DAILY_QUOTE、ADJ_FACTOR、DAILY_BASIC、WEEKLY_KLINE或MONTHLY_KLINE",
    )
