"""创建指数主数据与来源标识映射两张表（宪章 VI 标准治理）。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "005"
down_revision: str | None = "004"
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
        "index_current",
        _id(),
        _uuid("index_id", "规范指数标识（UUID，应用生成）"),
        sa.Column(
            "index_code",
            sa.String(32, collation="ascii_bin"),
            nullable=False,
            comment="规范指数代码（来源 ts_code，含 .SH/.SZ/.CSI/.SI 后缀）",
        ),
        _created(),
        _updated(),
        sa.UniqueConstraint("index_id", name="uq_index_current_index_id"),
        sa.UniqueConstraint("index_code", name="uq_index_current_index_code"),
        sa.CheckConstraint(
            "index_code LIKE '%.SH' OR index_code LIKE '%.SZ' "
            "OR index_code LIKE '%.CSI' OR index_code LIKE '%.SI' "
            "OR index_code LIKE '%.CI' OR index_code LIKE '%.NH' "
            "OR index_code LIKE '%.BJ' OR index_code LIKE '%.CNI'",
            name="ck_index_current_code_suffix",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
        mysql_comment="指数主数据（大盘指数、申万行业指数、中信指数）",
    )

    op.create_table(
        "index_provider_mapping",
        _id(),
        sa.Column(
            "provider_code",
            sa.String(32, collation="ascii_bin"),
            nullable=False,
            comment="数据来源代码（如 tushare）",
        ),
        sa.Column(
            "provider_security_id",
            sa.String(64, collation="ascii_bin"),
            nullable=False,
            comment="来源指数标识（ts_code）",
        ),
        _uuid("index_id", "规范指数标识（指向 index_current.index_id）"),
        _created(),
        _updated(),
        sa.ForeignKeyConstraint(
            ["index_id"],
            ["index_current.index_id"],
            name="fk_index_provider_mapping_index",
        ),
        sa.UniqueConstraint(
            "provider_code",
            "provider_security_id",
            name="uq_index_provider_mapping",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_bin",
        mysql_comment="指数来源标识映射（一个来源标识只映射一个规范指数标识）",
    )


def downgrade() -> None:
    op.drop_table("index_provider_mapping")
    op.drop_table("index_current")
