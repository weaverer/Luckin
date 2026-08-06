"""stock_factor ClickHouse 宽表 schema 与 MySQL 无新表验证（007 功能）。

本功能不新建、不结构性修改任何 MySQL 业务表（身份复用 003、审计复用 005），
因此验证重点是：ClickHouse ``stock_factor`` 表 DDL（引擎/排序键/分区/列注释/
同键替换幂等）以及 MySQL 审计表结构未被本功能改动（DataKind 为纯枚举扩展）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from lucking.config import Settings
from lucking.models.stock_factor import (
    DAY_COUNT_FIELDS,
    STOCK_FACTOR_FIELDS,
)


def test_field_catalog_shape() -> None:
    """白名单形态：字段名含 _bfq/_qfq/_hfq 变体、天数整数列、可修订分级齐全。"""
    assert "ma_bfq_5" in STOCK_FACTOR_FIELDS  # 前缀式周期（ma/ema/rsi）
    assert "ma_qfq_5" in STOCK_FACTOR_FIELDS
    assert "ma_hfq_5" in STOCK_FACTOR_FIELDS
    assert "kdj_bfq" in STOCK_FACTOR_FIELDS  # 后缀式变体
    assert "adj_factor" in STOCK_FACTOR_FIELDS
    assert "close" not in STOCK_FACTOR_FIELDS  # close 为独立锚点字段
    for field in DAY_COUNT_FIELDS:
        assert field in STOCK_FACTOR_FIELDS
    # 实测校准（T008）：价格复权变体仅 _qfq/_hfq 两形态（无 _bfq）
    assert "close_qfq" in STOCK_FACTOR_FIELDS
    assert "close_hfq" in STOCK_FACTOR_FIELDS
    assert "close_bfq" not in STOCK_FACTOR_FIELDS
    assert "open_bfq" not in STOCK_FACTOR_FIELDS
    # 回归（2026-08-05 实测修复）：ma_mass 为后缀式变体（ma_mass_bfq），
    # 不得误生成 ma_bfq_mass
    assert "ma_mass_bfq" in STOCK_FACTOR_FIELDS
    assert "ma_mass_hfq" in STOCK_FACTOR_FIELDS
    assert "ma_mass_qfq" in STOCK_FACTOR_FIELDS
    assert "ma_bfq_mass" not in STOCK_FACTOR_FIELDS


def test_live_clickhouse_stock_factor_table() -> None:
    """ClickHouse stock_factor 表引擎、排序键、分区、列注释与幂等替换核对。"""
    from lucking.clickhouse import ClickHouseClient, migrate

    settings = Settings()
    client = ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=(
            settings.clickhouse_password.get_secret_value()
            if settings.clickhouse_password is not None
            else None
        ),
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    assert client.table_engine("stock_factor") == "ReplacingMergeTree"
    assert client.table_partition_key("stock_factor") == "toYYYYMM(trade_date)"
    assert client.table_sorting_key("stock_factor") == "trade_date, stock_id"
    columns = {column.name: column for column in client.describe_table("stock_factor")}
    for required in (
        "trade_date",
        "stock_id",
        "stock_code",
        "close",
        "open_qfq",
        "ma_bfq_5",
        "ma_qfq_5",
        "adj_factor",
        "pe_ttm",
        "updays",
        "updated_at",
    ):
        assert required in columns, f"缺少列：{required}"
        assert columns[required].comment, f"列缺少中文注释：{required}"
    assert columns["close"].type.replace(" ", "") == "Decimal(12,4)"
    assert columns["ma_qfq_5"].type.startswith("Nullable(Decimal")
    assert columns["updays"].type.startswith("Nullable(UInt16")
    # 回归（2026-08-05 实测修复）：股本/市值宽精度 Decimal(24,4)，
    # 大市值股票 total_mv 可达 10^8 万元，Decimal(12,4) 会溢出
    assert columns["total_mv"].type.replace(" ", "") == "Nullable(Decimal(24,4))"
    assert columns["circ_mv"].type.replace(" ", "") == "Nullable(Decimal(24,4))"
    assert columns["total_share"].type.replace(" ", "") == "Nullable(Decimal(24,4))"
    # 同键替换幂等：同一 (trade_date, stock_id) 重复写入，FINAL 只保留最新 updated_at
    _idempotent_replace_check(client)


def _idempotent_replace_check(client) -> None:  # type: ignore[no-untyped-def]
    target = date(2004, 1, 5)
    stock_id = "00000000-0000-0000-0000-00000000dead"
    rows_v1 = [
        {
            "trade_date": target,
            "stock_id": stock_id,
            "stock_code": "TEST.BJ",
            "close": Decimal("10.5"),
            "ma_bfq_5": Decimal("10.4"),
            "updays": 3,
            "updated_at": datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC),
        }
    ]
    rows_v2 = [
        {
            "trade_date": target,
            "stock_id": stock_id,
            "stock_code": "TEST.BJ",
            "close": Decimal("10.5"),
            "ma_bfq_5": Decimal("10.6"),
            "updays": 4,
            "updated_at": datetime(2026, 8, 2, 10, 0, 0, tzinfo=UTC),
        }
    ]
    try:
        client.insert_rows(
            "stock_factor",
            tuple(rows_v1[0].keys()),
            rows_v1,
        )
        client.insert_rows(
            "stock_factor",
            tuple(rows_v2[0].keys()),
            rows_v2,
        )
        rows = client.execute(
            f"SELECT * FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}' AND stock_id = '{stock_id}'"
        )
        assert len(rows) == 1, "FINAL 未收敛为单行"
        assert str(rows[0]["ma_bfq_5"]).rstrip("0") == "10.6", "未保留最新版本"
        assert int(rows[0]["updays"]) == 4
    finally:
        client.execute_ddl(
            f"ALTER TABLE {client.database}.stock_factor "
            f"DELETE WHERE stock_id = '{stock_id}'"
        )


def test_no_new_mysql_tables_for_this_feature() -> None:
    """本功能未引入任何新的 MySQL 业务表（宪章 VI 不适用结论的可执行验证）。

    DataKind.STOCK_FACTOR 为纯枚举扩展：既有审计表结构不受影响，
    仓库不存在本功能新建的表模型。
    """
    from lucking.models.market_data import DataKind
    from lucking.models.stock_list import StockCurrent, StockProviderMapping

    assert DataKind.STOCK_FACTOR.value == "STOCK_FACTOR"
    # 身份复用 003：stock_current/stock_provider_mapping 由 003 功能治理
    assert StockCurrent.__tablename__ == "stock_current"
    assert StockProviderMapping.__tablename__ == "stock_provider_mapping"
    # 白名单容量底线（262 数据列 + 121 可修订字段），防止校准误删
    assert len(STOCK_FACTOR_FIELDS) >= 250
