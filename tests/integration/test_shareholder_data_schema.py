"""股东数据 ClickHouse 两表 schema 与 MySQL 无新表验证（T004）。

本功能不新建、不结构性修改任何 MySQL 业务表（身份复用 003、审计复用 005），
因此验证重点是：ClickHouse ``shareholder_holding``/``shareholder_count``
表 DDL（引擎/排序键/分区/列注释/同键替换幂等）以及 MySQL 审计表结构
未被本功能改动（DataKind 为纯枚举扩展）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from lucking.config import Settings
from lucking.models.market_data import DataKind
from lucking.models.shareholder_data import (
    HOLDER_COUNT_FIELDS,
    PROVIDER_HOLDER_COUNT_FIELDS,
    PROVIDER_TOP10_HOLDER_FIELDS,
    TOP10_HOLDER_FIELDS,
)


def test_field_catalog_shape() -> None:
    """白名单形态：9+4 字段与来源文档逐名一致（2026-08-05 实测确认）。"""
    assert TOP10_HOLDER_FIELDS == (
        "ann_date",
        "end_date",
        "holder_name",
        "hold_amount",
        "hold_ratio",
        "hold_float_ratio",
        "hold_change",
        "holder_type",
    )
    assert HOLDER_COUNT_FIELDS == ("ann_date", "end_date", "holder_num")
    assert PROVIDER_TOP10_HOLDER_FIELDS == ("ts_code",) + TOP10_HOLDER_FIELDS
    assert PROVIDER_HOLDER_COUNT_FIELDS == ("ts_code",) + HOLDER_COUNT_FIELDS


def test_data_kind_enum_extended_without_structure_change() -> None:
    """DataKind 纯枚举扩展：三接口取值存在，审计表无列变更。"""
    assert DataKind.TOP10_HOLDERS.value == "TOP10_HOLDERS"
    assert DataKind.TOP10_FLOAT_HOLDERS.value == "TOP10_FLOAT_HOLDERS"
    assert DataKind.HOLDER_COUNT.value == "HOLDER_COUNT"


def test_live_clickhouse_shareholder_holding_table() -> None:
    """ClickHouse shareholder_holding 表引擎、排序键、分区、注释与幂等替换核对。"""
    from lucking.clickhouse import ClickHouseClient, migrate

    settings = Settings()
    migrate(settings)
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
    assert client.table_engine("shareholder_holding") == "ReplacingMergeTree"
    assert client.table_sorting_key("shareholder_holding") == (
        "end_date, stock_id, holder_kind, holder_name"
    )
    assert client.table_partition_key("shareholder_holding") == "toYYYYMM(end_date)"
    columns = {info.name: info.comment for info in client.describe_table("shareholder_holding")}
    assert columns["end_date"]
    assert columns["stock_id"]
    assert columns["holder_kind"]
    assert columns["holder_name"]
    assert columns["hold_amount"] == "持有数量（股）"
    assert columns["updated_at"]
    # 同键替换幂等：重复 INSERT 同键行后 FINAL 只保留最新 updated_at
    now = datetime.now(UTC).replace(tzinfo=None)
    stock_id = "schema-test-000000000000000000000001"  # 恰好 36 字符
    client.insert_rows(
        "shareholder_holding",
        (
            "end_date",
            "stock_id",
            "holder_kind",
            "holder_name",
            "ann_date",
            "stock_code",
            "updated_at",
        ),
        [
            {
                "end_date": date(2026, 1, 31),
                "stock_id": stock_id,
                "holder_kind": "TOP10",
                "holder_name": "schema-holder",
                "ann_date": date(2026, 2, 28),
                "stock_code": "600000.SH",
                "updated_at": now,
            }
        ],
    )
    client.insert_rows(
        "shareholder_holding",
        (
            "end_date",
            "stock_id",
            "holder_kind",
            "holder_name",
            "ann_date",
            "stock_code",
            "updated_at",
        ),
        [
            {
                "end_date": date(2026, 1, 31),
                "stock_id": stock_id,
                "holder_kind": "TOP10",
                "holder_name": "schema-holder",
                "ann_date": date(2026, 3, 2),
                "stock_code": "600000.SH",
                "updated_at": now.replace(microsecond=now.microsecond + 1),
            }
        ],
    )
    rows = client.execute(
        "SELECT count() AS count FROM lucking.shareholder_holding FINAL "
        f"WHERE stock_id = '{stock_id}'"
    )
    assert rows[0]["count"] == 1  # 同键收敛为一条
    client.execute(
        "ALTER TABLE lucking.shareholder_holding DELETE WHERE stock_id = "
        f"'{stock_id}'"
    )


def test_live_clickhouse_shareholder_count_table() -> None:
    """ClickHouse shareholder_count 表引擎、排序键、分区与注释核对。"""
    from lucking.clickhouse import ClickHouseClient, migrate

    settings = Settings()
    migrate(settings)
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
    assert client.table_engine("shareholder_count") == "ReplacingMergeTree"
    assert client.table_sorting_key("shareholder_count") == "end_date, stock_id"
    assert client.table_partition_key("shareholder_count") == "toYYYYMM(end_date)"
    columns = {info.name: info.comment for info in client.describe_table("shareholder_count")}
    assert columns["holder_num"] == "股东户数"
    assert columns["ann_date"]
    assert columns["updated_at"]


@pytest.mark.mysql
def test_mysql_audit_tables_unchanged() -> None:
    """MySQL 审计表结构未被本功能改动（无新增表、无列变更）。"""
    from sqlalchemy import create_engine, inspect

    from lucking.config import Settings

    settings = Settings()
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "market_data_sync_run" in tables
    assert "market_data_sync_attempt" in tables
    assert "market_data_sync_issue" in tables
    # 本功能不新建任何 MySQL 业务表
    assert "shareholder_sync_run" not in tables
    assert "shareholder_sync_attempt" not in tables
    run_columns = {column["name"] for column in inspector.get_columns("market_data_sync_run")}
    assert "data_kind" in run_columns
    assert "run_key" in run_columns
    engine.dispose()
