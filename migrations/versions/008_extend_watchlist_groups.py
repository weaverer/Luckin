"""为自选分组增加备注、标签和无限分组支持。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watchlist_group",
        sa.Column(
            "notes",
            sa.String(1000),
            nullable=False,
            server_default="待补充",
            comment="分组备注",
        ),
    )
    op.add_column(
        "watchlist_group",
        sa.Column(
            "tags",
            mysql.JSON(),
            nullable=True,
            comment="分组标签",
        ),
    )
    op.execute("UPDATE watchlist_group SET tags = JSON_ARRAY('默认') WHERE tags IS NULL")
    op.alter_column("watchlist_group", "tags", existing_type=mysql.JSON(), nullable=False)
    op.alter_column("watchlist_group", "notes", server_default=None)


def downgrade() -> None:
    op.drop_column("watchlist_group", "tags")
    op.drop_column("watchlist_group", "notes")
