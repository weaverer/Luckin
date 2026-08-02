"""创建行情数据同步运行、执行尝试和质量问题三张审计表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column(
        "id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="主键ID"
    )


def _uuid(name: str, comment: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(36, collation="ascii_bin"), nullable=nullable, comment=comment)


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


def upgrade() -> None:
    op.create_table(
        "market_data_sync_run",
        _id(),
        _uuid("run_id", "同步运行业务UUID"),
        sa.Column(
            "run_key",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="规范运行身份的SHA-256摘要",
        ),
        sa.Column(
            "data_kind",
            sa.String(16, collation="ascii_bin"),
            nullable=False,
            comment="数据类：DAILY_QUOTE、ADJ_FACTOR、DAILY_BASIC、WEEKLY_KLINE或MONTHLY_KLINE",
        ),
        sa.Column(
            "run_kind",
            sa.String(12, collation="ascii_bin"),
            nullable=False,
            comment="运行类型：计划运行或历史回补",
        ),
        sa.Column(
            "schedule_slug",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="计划运行标识；回补为空",
        ),
        sa.Column(
            "scheduled_for",
            mysql.DATETIME(fsp=6),
            nullable=True,
            comment="计划运行原定UTC时点；回补为空",
        ),
        sa.Column(
            "backfill_batch_id",
            sa.String(128, collation="ascii_bin"),
            nullable=True,
            comment="历史回补批次幂等键；计划运行为空",
        ),
        sa.Column(
            "target_trade_date", sa.Date(), nullable=False, comment="运行目标交易日"
        ),
        sa.Column(
            "scope_fingerprint",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="交易日、数据类与契约版本的审计范围摘要",
        ),
        sa.Column(
            "status",
            sa.String(12, collation="ascii_bin"),
            nullable=False,
            comment="运行状态：待执行、执行中、成功或失败",
        ),
        sa.Column(
            "attempt_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="已创建执行尝试数",
        ),
        _uuid("successful_attempt_id", "唯一成功执行尝试的业务UUID", nullable=True),
        sa.Column(
            "published_at", mysql.DATETIME(fsp=6), nullable=True, comment="成功发布的UTC时间"
        ),
        _created(),
        _updated(),
        sa.UniqueConstraint("run_id", name="uq_market_data_run_uuid"),
        sa.UniqueConstraint("run_key", name="uq_market_data_run_key"),
        sa.Index(
            "ix_market_data_run_filter",
            "data_kind",
            "target_trade_date",
            "status",
        ),
        sa.CheckConstraint(
            "(run_kind = 'SCHEDULED' AND schedule_slug IS NOT NULL AND scheduled_for IS NOT NULL "
            "AND backfill_batch_id IS NULL) OR (run_kind = 'BACKFILL' AND schedule_slug IS NULL "
            "AND scheduled_for IS NULL AND backfill_batch_id IS NOT NULL)",
            name="ck_market_data_run_identity",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_market_data_run_status",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
        mysql_comment="行情数据同步运行",
    )

    op.create_table(
        "market_data_sync_attempt",
        _id(),
        _uuid("attempt_id", "执行尝试业务UUID"),
        _uuid("run_id", "所属同步运行的业务UUID"),
        sa.Column(
            "attempt_no", mysql.INTEGER(unsigned=True), nullable=False, comment="运行内从1递增"
        ),
        sa.Column(
            "flow_run_id",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="Prefect工作流运行标识",
        ),
        sa.Column(
            "provider_code",
            sa.String(32, collation="ascii_bin"),
            nullable=False,
            comment="本次选中的数据来源代码",
        ),
        sa.Column(
            "status",
            sa.String(12, collation="ascii_bin"),
            nullable=False,
            comment="尝试状态：执行中、成功、失败或已放弃",
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False, comment="实际开始的UTC时间"),
        sa.Column(
            "lease_expires_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            comment="运行租约到期的UTC时间",
        ),
        sa.Column(
            "completed_at", mysql.DATETIME(fsp=6), nullable=True, comment="进入终态的UTC时间"
        ),
        sa.Column(
            "provider_request_count",
            mysql.SMALLINT(unsigned=True),
            nullable=False,
            server_default="0",
            comment="实际数据来源请求次数",
        ),
        sa.Column(
            "provider_retry_count",
            mysql.SMALLINT(unsigned=True),
            nullable=False,
            server_default="0",
            comment="初次调用之外的重试次数",
        ),
        sa.Column(
            "provider_page_count",
            mysql.SMALLINT(unsigned=True),
            nullable=False,
            server_default="0",
            comment="已成功取得的提取批次数，包含空终止批",
        ),
        sa.Column(
            "provider_page_limit",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="本次批次行数上限",
        ),
        sa.Column(
            "provider_last_page_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="终止批次原始行数",
        ),
        sa.Column(
            "received_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="来源行数",
        ),
        sa.Column(
            "valid_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="去重后有效候选数",
        ),
        sa.Column(
            "added_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="新增记录数",
        ),
        sa.Column(
            "updated_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="业务字段更新数",
        ),
        sa.Column(
            "unchanged_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="已确认但业务字段未变化数",
        ),
        sa.Column(
            "duplicate_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="已解决的完全相同重复数",
        ),
        sa.Column(
            "invalid_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="无效记录数",
        ),
        sa.Column(
            "conflict_count",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            server_default="0",
            comment="身份或字段冲突数",
        ),
        sa.Column(
            "candidate_digest",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="规范排序候选的SHA-256摘要",
        ),
        sa.Column(
            "error_category",
            sa.String(48, collation="ascii_bin"),
            nullable=True,
            comment="统一安全错误类别",
        ),
        sa.Column("error_summary", sa.String(500), nullable=True, comment="脱敏摘要"),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["market_data_sync_run.run_id"],
            name="fk_market_data_attempt_run",
        ),
        sa.UniqueConstraint("attempt_id", name="uq_market_data_attempt_uuid"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_market_data_attempt_no"),
        sa.UniqueConstraint("flow_run_id", name="uq_market_data_attempt_flow"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','ABANDONED')",
            name="ck_market_data_attempt_status",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
        mysql_comment="行情数据同步执行尝试",
    )
    op.create_foreign_key(
        "fk_market_data_run_success_attempt",
        "market_data_sync_run",
        "market_data_sync_attempt",
        ["successful_attempt_id"],
        ["attempt_id"],
    )

    op.create_table(
        "market_data_sync_issue",
        _id(),
        _uuid("issue_id", "质量问题业务UUID"),
        _uuid("attempt_id", "所属执行尝试的业务UUID"),
        sa.Column(
            "category", sa.String(48, collation="ascii_bin"), nullable=False, comment="统一问题类别"
        ),
        sa.Column(
            "provider_security_id_hash",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="可选数据来源股票标识摘要",
        ),
        sa.Column(
            "venue_code",
            sa.String(4, collation="ascii_bin"),
            nullable=True,
            comment="安全定位用交易场所代码",
        ),
        sa.Column(
            "security_code",
            sa.String(32, collation="ascii_bin"),
            nullable=True,
            comment="安全定位代码",
        ),
        sa.Column(
            "field_name", sa.String(64, collation="ascii_bin"), nullable=True, comment="规范字段名"
        ),
        sa.Column("safe_summary", sa.String(500), nullable=False, comment="白名单脱敏摘要"),
        sa.Column(
            "payload_hash",
            sa.String(64, collation="ascii_bin"),
            nullable=True,
            comment="原始候选摘要，不保存原文",
        ),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["market_data_sync_attempt.attempt_id"],
            name="fk_market_data_issue_attempt",
        ),
        sa.UniqueConstraint("issue_id", name="uq_market_data_issue_uuid"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
        mysql_comment="行情数据同步质量问题",
    )
    op.create_index(
        "ix_market_data_issue_attempt", "market_data_sync_issue", ["attempt_id"]
    )


def downgrade() -> None:
    op.drop_table("market_data_sync_issue")
    op.drop_constraint(
        "fk_market_data_run_success_attempt",
        "market_data_sync_run",
        type_="foreignkey",
    )
    op.drop_table("market_data_sync_attempt")
    op.drop_table("market_data_sync_run")
