"""指数身份表（index_current / index_provider_mapping）schema 三方一致验证。"""

import os
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, inspect, text

from lucking.config import Settings
from lucking.models.index_factor import IndexCurrent, IndexProviderMapping

TABLES = (
    IndexCurrent.__table__,
    IndexProviderMapping.__table__,
)


def test_orm_and_migration_declare_governed_physical_and_business_keys() -> None:
    migration = Path(
        "migrations/versions/005_create_index_identity_tables.py"
    ).read_text()
    for table in TABLES:
        assert [column.name for column in table.primary_key.columns] == ["id"]
        assert table.c.id.type.python_type is int
        assert all(column.comment for column in table.columns)
    for comment in (
        "指数主数据（大盘指数、申万行业指数、中信指数）",
        "指数来源标识映射（一个来源标识只映射一个规范指数标识）",
    ):
        assert comment in migration
    assert "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in migration
    unique_by_table = {
        table.name: {
            frozenset(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        for table in TABLES
    }
    assert {"index_id"} in unique_by_table["index_current"]
    assert {"index_code"} in unique_by_table["index_current"]
    assert {"provider_code", "provider_security_id"} in unique_by_table[
        "index_provider_mapping"
    ]


@pytest.mark.mysql
def test_live_mysql_has_bigint_autoincrement_and_chinese_comments() -> None:
    url = _mysql_url()
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
        expected = {table.name for table in TABLES}
        if not expected <= names:
            pytest.skip("测试库尚未 upgrade 到 revision 005")
        with engine.connect() as connection:
            for table in TABLES:
                ddl = connection.execute(text(f"SHOW CREATE TABLE `{table.name}`")).one()[1]
                assert "`id` bigint" in ddl.lower()
                assert "AUTO_INCREMENT" in ddl
                assert "ON UPDATE CURRENT_TIMESTAMP" in ddl
                assert table.comment in ddl
                live_columns = {
                    column["name"]: column for column in inspect(connection).get_columns(table.name)
                }
                assert set(live_columns) == set(table.c.keys())
                for column in table.columns:
                    assert live_columns[column.name]["comment"] == column.comment
                assert inspect(connection).get_pk_constraint(table.name)["constrained_columns"] == [
                    "id"
                ]
                unique_keys = inspect(connection).get_unique_constraints(table.name)
                for constraint in table.constraints:
                    if hasattr(constraint, "unique") and constraint.unique:
                        assert any(
                            set(constraint.columns) == set(unique["column_names"])
                            for unique in unique_keys
                        )
    finally:
        engine.dispose()


@pytest.mark.mysql
def _mysql_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    if os.getenv("LUCKING_USE_LOCAL_MYSQL_TESTS") == "1":
        return Settings().database_url
    pytest.skip("未配置 TEST_DATABASE_URL")


def test_live_mysql_clickhouse_index_factor_table() -> None:
    """ClickHouse index_factor 表引擎、排序键、分区与列注释核对（依赖 migrate）。"""
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
    assert client.table_engine("index_factor") == "ReplacingMergeTree"
    assert client.table_partition_key("index_factor") == "toYYYYMM(trade_date)"
    assert client.table_sorting_key("index_factor") == "trade_date, index_id"
    columns = {column.name: column for column in client.describe_table("index_factor")}
    for required in (
        "trade_date",
        "index_id",
        "index_code",
        "open",
        "close",
        "ma_5",
        "macd",
        "boll_upper",
        "updays",
        "updated_at",
    ):
        assert required in columns, f"缺少列：{required}"
        assert columns[required].comment
    assert columns["ma_5"].type.startswith("Nullable(Decimal")
    assert columns["updays"].type.startswith("Nullable(UInt16")
