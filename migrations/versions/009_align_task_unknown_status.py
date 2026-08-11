"""将任务合法跳过归一为未知状态。"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "daily_task_summary",
        "skipped_count",
        new_column_name="unknown_count",
        existing_type=mysql.INTEGER(unsigned=True),
        existing_nullable=False,
        existing_comment="合法跳过任务数",
        comment="未知状态任务数",
    )
    op.drop_constraint(
        "ck_daily_task_summary_item_status",
        "daily_task_summary_item",
        type_="check",
    )
    op.execute(
        "UPDATE daily_task_summary_item SET status = 'UNKNOWN' WHERE status = 'SKIPPED'"
    )
    op.create_check_constraint(
        "ck_daily_task_summary_item_status",
        "daily_task_summary_item",
        "status IN ('SUCCEEDED','PARTIAL','FAILED','RUNNING','UNKNOWN','NOT_RUN')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_daily_task_summary_item_status",
        "daily_task_summary_item",
        type_="check",
    )
    op.execute(
        "UPDATE daily_task_summary_item SET status = 'SKIPPED' WHERE status = 'UNKNOWN'"
    )
    op.create_check_constraint(
        "ck_daily_task_summary_item_status",
        "daily_task_summary_item",
        "status IN ('SUCCEEDED','PARTIAL','FAILED','RUNNING','SKIPPED','NOT_RUN')",
    )
    op.alter_column(
        "daily_task_summary",
        "unknown_count",
        new_column_name="skipped_count",
        existing_type=mysql.INTEGER(unsigned=True),
        existing_nullable=False,
        existing_comment="未知状态任务数",
        comment="合法跳过任务数",
    )
