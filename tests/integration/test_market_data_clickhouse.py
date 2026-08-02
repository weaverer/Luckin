"""ClickHouse 五表 schema 校验与同键替换幂等测试（真实 lucking 库 + 唯一标记行）。"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from lucking.clickhouse import (
    CLICKHOUSE_TABLE_DDL,
    ClickHouseClient,
    ClickHousePersistenceError,
    migrate,
)
from lucking.config import Settings


def _marker_date() -> str:
    """每个测试运行使用唯一的 1990 年日期，与历史数据及历史测试残留永不重叠。"""
    return (date(1990, 1, 1) + timedelta(days=int(uuid4().hex[:5], 16) % 360)).isoformat()


@pytest.fixture
def clickhouse() -> Iterator[tuple[ClickHouseClient, Settings]]:
    settings = Settings()
    client = _build_client(settings)
    try:
        client.execute("SELECT 1")
    except ClickHousePersistenceError as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    yield client, settings


@pytest.mark.mysql
def test_migrate_creates_five_tables_with_governed_engine_keys_and_comments(
    clickhouse: tuple[ClickHouseClient, Settings],
) -> None:
    client, settings = clickhouse
    created = migrate(settings)
    assert set(created) == set(CLICKHOUSE_TABLE_DDL)
    # 幂等：重复 migrate 不报错
    assert set(migrate(settings)) == set(CLICKHOUSE_TABLE_DDL)
    for table in ("daily_quote", "adj_factor", "daily_basic", "weekly_kline", "monthly_kline"):
        assert client.table_engine(table) == "ReplacingMergeTree"
        assert "toYYYYMM(trade_date)" in client.table_partition_key(table)
        assert "trade_date, stock_id" in client.table_sorting_key(table)
        columns = {column.name: column for column in client.describe_table(table)}
        assert columns["trade_date"].type == "Date"
        assert columns["stock_id"].type == "FixedString(36)"
        assert columns["updated_at"].type == "DateTime64(3)"
        expected_trade_date_comment = (
            "周期最后交易日（每周五或该周最后交易日）"
            if table == "weekly_kline"
            else "周期最后交易日（月末最后一个交易日）"
            if table == "monthly_kline"
            else "交易日"
        )
        assert columns["trade_date"].comment == expected_trade_date_comment
        assert columns["stock_id"].comment == "项目规范股票业务UUID"
    basic = {column.name: column for column in client.describe_table("daily_basic")}
    assert basic["pe"].type.replace(" ", "") == "Nullable(Decimal(16,4))"
    assert basic["limit_status"].type == "Nullable(UInt8)"
    for table in ("weekly_kline", "monthly_kline"):
        kline = {column.name: column for column in client.describe_table(table)}
        assert kline["close"].type.replace(" ", "") == "Decimal(12,4)"
        assert kline["open"].type.replace(" ", "") == "Decimal(12,4)"
        assert kline["end_date"].type == "Nullable(Date)"
        assert "qfq_open" not in kline
        assert "hfq_close" not in kline


@pytest.mark.mysql
def test_same_key_replacing_merge_keeps_latest_version(
    clickhouse: tuple[ClickHouseClient, Settings],
) -> None:
    client, settings = clickhouse
    migrate(settings)
    marker = _marker_date()
    stock_a = str(uuid4())
    stock_b = str(uuid4())
    older = datetime(1990, 1, 2, 1, 0, 0, tzinfo=UTC)
    newer = datetime(1990, 1, 2, 2, 0, 0, tzinfo=UTC)
    base_a = {
        "trade_date": marker,
        "stock_id": stock_a,
        "venue_code": "XSHG",
        "security_code": "000001",
    }
    base_b = {
        "trade_date": marker,
        "stock_id": stock_b,
        "venue_code": "XSHE",
        "security_code": "000002",
    }
    table = f"{settings.clickhouse_database}.daily_quote"
    try:
        client.insert_rows(
            "daily_quote",
            ("trade_date", "stock_id", "venue_code", "security_code", "close", "updated_at"),
            [
                {**base_a, "close": "10.0000", "updated_at": older},
                {**base_a, "close": "11.0000", "updated_at": newer},
                {**base_b, "close": "20.0000", "updated_at": older},
            ],
        )
        rows = client.execute(
            f"SELECT stock_id, close FROM {table} FINAL "
            f"WHERE trade_date = '{marker}' AND stock_id = '{stock_a}'"
        )
        assert len(rows) == 1
        assert Decimal(rows[0]["close"]) == Decimal("11.0000")
        rows_all = client.execute(
            f"SELECT stock_id FROM {table} FINAL WHERE trade_date = '{marker}'"
        )
        assert {row["stock_id"] for row in rows_all} == {stock_a, stock_b}
    finally:
        client.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE stock_id IN ('{stock_a}', '{stock_b}') "
            "SETTINGS mutations_sync = 1"
        )


def _build_client(settings: Settings) -> ClickHouseClient:
    password = (
        settings.clickhouse_password.get_secret_value()
        if settings.clickhouse_password is not None
        else None
    )
    return ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=password,
    )
