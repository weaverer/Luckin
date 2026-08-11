"""创建投资工作台账号、个人配置与任务汇总表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column(
        "id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="主键ID"
    )


def _created() -> sa.Column:
    return sa.Column(
        "created_at",
        mysql.DATETIME(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )


def _updated() -> sa.Column:
    return sa.Column(
        "updated_at",
        mysql.DATETIME(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


def _uuid(name: str, comment: str) -> sa.Column:
    return sa.Column(name, sa.String(36, collation="ascii_bin"), nullable=False, comment=comment)


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


def upgrade() -> None:
    op.create_table(
        "app_user",
        _id(),
        _uuid("user_id", "用户业务UUID"),
        sa.Column(
            "username", sa.String(64, collation="ascii_bin"), nullable=False, comment="规范化登录名"
        ),
        sa.Column("display_name", sa.String(80), nullable=False, comment="用户显示名称"),
        sa.Column(
            "password_hash",
            sa.String(255, collation="ascii_bin"),
            nullable=False,
            comment="Argon2id密码哈希",
        ),
        sa.Column(
            "status", sa.String(16, collation="ascii_bin"), nullable=False, comment="账号状态"
        ),
        sa.Column(
            "password_changed_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            comment="最近密码变更UTC时间",
        ),
        sa.Column(
            "last_login_at", mysql.DATETIME(fsp=6), nullable=True, comment="最近成功登录UTC时间"
        ),
        _created(),
        _updated(),
        sa.UniqueConstraint("user_id", name="uq_app_user_user_id"),
        sa.UniqueConstraint("username", name="uq_app_user_username"),
        sa.CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_app_user_status"),
        mysql_comment="应用用户",
        **TABLE_OPTIONS,
    )

    op.create_table(
        "important_date",
        _id(),
        _uuid("important_date_id", "重要日业务UUID"),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False, comment="所属用户主键ID"),
        sa.Column("event_date", sa.Date(), nullable=False, comment="重要日期"),
        sa.Column("title", sa.String(120), nullable=False, comment="重要日标题"),
        sa.Column("title_key", sa.String(120), nullable=False, comment="标题规范化唯一键"),
        sa.Column("notes", sa.String(1000), nullable=True, comment="重要日备注"),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="fk_important_date_user"),
        sa.UniqueConstraint("important_date_id", name="uq_important_date_id"),
        sa.UniqueConstraint(
            "user_id", "event_date", "title_key", name="uq_important_date_owner_date_title"
        ),
        mysql_comment="用户重要日",
        **TABLE_OPTIONS,
    )
    op.create_index("ix_important_date_owner_date", "important_date", ["user_id", "event_date"])

    op.create_table(
        "watchlist_group",
        _id(),
        _uuid("group_id", "自选分组业务UUID"),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False, comment="所属用户主键ID"),
        sa.Column("name", sa.String(80), nullable=False, comment="分组名称"),
        sa.Column("name_key", sa.String(80), nullable=False, comment="分组名称规范化唯一键"),
        sa.Column(
            "sort_order", mysql.INTEGER(unsigned=True), nullable=False, comment="分组显示顺序"
        ),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], name="fk_watchlist_group_user"),
        sa.UniqueConstraint("group_id", name="uq_watchlist_group_id"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_watchlist_group_owner_name"),
        mysql_comment="用户自选分组",
        **TABLE_OPTIONS,
    )
    op.create_index("ix_watchlist_group_owner_sort", "watchlist_group", ["user_id", "sort_order"])

    op.create_table(
        "watchlist_member",
        _id(),
        _uuid("member_id", "自选成员业务UUID"),
        sa.Column(
            "group_id", mysql.BIGINT(unsigned=True), nullable=False, comment="所属自选分组主键ID"
        ),
        sa.Column(
            "stock_id",
            sa.String(36, collation="ascii_bin"),
            nullable=False,
            comment="规范股票业务UUID",
        ),
        sa.Column(
            "sort_order", mysql.INTEGER(unsigned=True), nullable=False, comment="股票显示顺序"
        ),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["group_id"], ["watchlist_group.id"], name="fk_watchlist_member_group"
        ),
        sa.ForeignKeyConstraint(
            ["stock_id"], ["stock_current.stock_id"], name="fk_watchlist_member_stock"
        ),
        sa.UniqueConstraint("member_id", name="uq_watchlist_member_id"),
        sa.UniqueConstraint("group_id", "stock_id", name="uq_watchlist_member_group_stock"),
        mysql_comment="自选分组股票成员",
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_watchlist_member_group_sort", "watchlist_member", ["group_id", "sort_order"]
    )

    op.create_table(
        "daily_task_summary",
        _id(),
        _uuid("summary_id", "汇总业务UUID"),
        sa.Column("business_date", sa.Date(), nullable=False, comment="北京时间业务日期"),
        sa.Column(
            "scheduled_for", mysql.DATETIME(fsp=6), nullable=False, comment="原定汇总UTC时点"
        ),
        sa.Column(
            "status", sa.String(16, collation="ascii_bin"), nullable=False, comment="汇总状态"
        ),
        sa.Column(
            "notification_status",
            sa.String(16, collation="ascii_bin"),
            nullable=False,
            comment="通知状态",
        ),
        sa.Column(
            "total_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="纳入统计的任务总数",
        ),
        sa.Column(
            "succeeded_count", mysql.INTEGER(unsigned=True), nullable=False, comment="成功任务数"
        ),
        sa.Column(
            "partial_count", mysql.INTEGER(unsigned=True), nullable=False, comment="部分完成任务数"
        ),
        sa.Column(
            "failed_count", mysql.INTEGER(unsigned=True), nullable=False, comment="失败任务数"
        ),
        sa.Column(
            "running_count", mysql.INTEGER(unsigned=True), nullable=False, comment="运行中任务数"
        ),
        sa.Column(
            "skipped_count", mysql.INTEGER(unsigned=True), nullable=False, comment="合法跳过任务数"
        ),
        sa.Column(
            "not_run_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="应执行但未执行任务数",
        ),
        sa.Column(
            "snapshot_digest",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="汇总内容SHA-256摘要",
        ),
        sa.Column("generated_at", mysql.DATETIME(fsp=6), nullable=True, comment="汇总完成UTC时间"),
        sa.Column(
            "notified_at", mysql.DATETIME(fsp=6), nullable=True, comment="最近成功通知UTC时间"
        ),
        _created(),
        _updated(),
        sa.UniqueConstraint("summary_id", name="uq_daily_task_summary_id"),
        sa.UniqueConstraint("business_date", name="uq_daily_task_summary_business_date"),
        sa.CheckConstraint(
            "status IN ('BUILDING','READY','FAILED')", name="ck_daily_task_summary_status"
        ),
        sa.CheckConstraint(
            "notification_status IN ('PENDING','SENDING','SENT','FAILED')",
            name="ck_daily_task_summary_notification_status",
        ),
        mysql_comment="每日任务汇总",
        **TABLE_OPTIONS,
    )

    op.create_table(
        "daily_task_summary_item",
        _id(),
        _uuid("item_id", "汇总明细业务UUID"),
        sa.Column(
            "summary_id", mysql.BIGINT(unsigned=True), nullable=False, comment="所属每日汇总主键ID"
        ),
        sa.Column(
            "task_key",
            sa.String(128, collation="ascii_bin"),
            nullable=False,
            comment="计划任务规范键",
        ),
        sa.Column(
            "schedule_slug",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="计划调度标识",
        ),
        sa.Column("display_name", sa.String(120), nullable=False, comment="任务显示名称"),
        sa.Column(
            "source_domain",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="运行数据所属领域",
        ),
        sa.Column(
            "status", sa.String(16, collation="ascii_bin"), nullable=False, comment="归一状态"
        ),
        sa.Column(
            "source_run_id",
            sa.String(128, collation="ascii_bin"),
            nullable=True,
            comment="来源运行业务标识",
        ),
        sa.Column(
            "source_flow_run_id",
            sa.String(128, collation="ascii_bin"),
            nullable=True,
            comment="Prefect Flow Run标识",
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True, comment="任务开始UTC时间"),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True, comment="任务完成UTC时间"),
        sa.Column(
            "record_count", mysql.BIGINT(unsigned=True), nullable=True, comment="成功处理记录数"
        ),
        sa.Column(
            "error_category",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="安全错误分类",
        ),
        sa.Column("error_summary", sa.String(500), nullable=True, comment="脱敏错误摘要"),
        sa.Column("observed_at", mysql.DATETIME(fsp=6), nullable=False, comment="状态观察UTC时间"),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["summary_id"], ["daily_task_summary.id"], name="fk_daily_task_summary_item_summary"
        ),
        sa.UniqueConstraint("item_id", name="uq_daily_task_summary_item_id"),
        sa.UniqueConstraint("summary_id", "task_key", name="uq_daily_task_summary_item_task"),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED','RUNNING','SKIPPED','NOT_RUN')",
            name="ck_daily_task_summary_item_status",
        ),
        mysql_comment="每日任务汇总明细",
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_daily_task_summary_item_status", "daily_task_summary_item", ["summary_id", "status"]
    )

    op.create_table(
        "daily_task_notification_attempt",
        _id(),
        _uuid("attempt_id", "通知尝试业务UUID"),
        sa.Column(
            "summary_id", mysql.BIGINT(unsigned=True), nullable=False, comment="所属每日汇总主键ID"
        ),
        sa.Column(
            "attempt_no", mysql.INTEGER(unsigned=True), nullable=False, comment="汇总内尝试序号"
        ),
        sa.Column(
            "trigger_kind", sa.String(16, collation="ascii_bin"), nullable=False, comment="触发类型"
        ),
        sa.Column(
            "status", sa.String(16, collation="ascii_bin"), nullable=False, comment="尝试状态"
        ),
        sa.Column(
            "provider_code",
            sa.String(32, collation="ascii_bin"),
            nullable=False,
            comment="通知实现代码",
        ),
        sa.Column(
            "response_status",
            mysql.INTEGER(unsigned=True),
            nullable=True,
            comment="外部HTTP响应状态",
        ),
        sa.Column(
            "error_category",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="安全错误分类",
        ),
        sa.Column("error_summary", sa.String(500), nullable=True, comment="脱敏错误摘要"),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False, comment="尝试开始UTC时间"),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True, comment="尝试完成UTC时间"),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["summary_id"],
            ["daily_task_summary.id"],
            name="fk_daily_task_notification_attempt_summary",
        ),
        sa.UniqueConstraint("attempt_id", name="uq_daily_task_notification_attempt_id"),
        sa.UniqueConstraint(
            "summary_id", "attempt_no", name="uq_daily_task_notification_attempt_no"
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('AUTOMATIC','MANUAL_RETRY')",
            name="ck_daily_task_notification_attempt_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED')",
            name="ck_daily_task_notification_attempt_status",
        ),
        mysql_comment="每日任务通知尝试",
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_daily_task_notification_attempt_summary",
        "daily_task_notification_attempt",
        ["summary_id", "attempt_no"],
    )


def downgrade() -> None:
    op.drop_table("daily_task_notification_attempt")
    op.drop_table("daily_task_summary_item")
    op.drop_table("daily_task_summary")
    op.drop_table("watchlist_member")
    op.drop_table("watchlist_group")
    op.drop_table("important_date")
    op.drop_table("app_user")
