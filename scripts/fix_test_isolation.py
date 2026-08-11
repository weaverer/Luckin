"""临时脚本：测试改为按测试专用 stock_id 前缀隔离（避免与用户回补的真实数据冲突）。"""

# ruff: noqa: E501 - source-code replacement fixtures intentionally preserve full lines.


def patch(path: str, pairs: list[tuple[str, str]]) -> None:
    with open(path, encoding="utf-8") as stream:
        content = stream.read()
    for old, new in pairs:
        count = content.count(old)
        assert count >= 1, f"{path}: 未命中: {old[:70]!r}"
        content = content.replace(old, new)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(content)
    print(f"{path}: ok")


# ---------- test_market_data_flow.py ----------
patch(
    "tests/integration/test_market_data_flow.py",
    [
        # _count 改为按测试前缀过滤（真实数据使用 UUID stock_id，不冲突）
        (
            "def _count(clickhouse: ClickHouseClient, data_kind: DataKind, target: date) -> int:\n"
            "    return MarketDataClickHouseRepository(clickhouse).count(data_kind, target)",
            "def _count(\n"
            "    clickhouse: ClickHouseClient,\n"
            "    data_kind: DataKind,\n"
            "    target: date,\n"
            '    prefix: str = "it-",\n'
            ") -> int:\n"
            "    table = MarketDataClickHouseRepository(clickhouse).TABLE_BY_KIND[data_kind]\n"
            "    rows = clickhouse.execute(\n"
            '        f"SELECT count() AS n FROM {clickhouse.database}.{table} FINAL "\n'
            "        f\"WHERE trade_date = '{target.isoformat()}' AND stock_id LIKE '{prefix}%'\"\n"
            "    )\n"
            '    return int(rows[0]["n"])',
        ),
        # 清理：按 trade_date 删除改为按测试前缀删除，避免误删用户回补数据
        (
            "clickhouse.execute_ddl(\n"
            "            f\"ALTER TABLE {table} DELETE WHERE trade_date = '{_TARGET}' \"\n"
            '            "SETTINGS mutations_sync = 1"\n'
            "        )",
            "clickhouse.execute_ddl(\n"
            "            f\"ALTER TABLE {table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"\n'
            "        )",
        ),
        (
            "f\"ALTER TABLE {clickhouse.database}.adj_factor DELETE WHERE trade_date = '{_TARGET}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {clickhouse.database}.adj_factor DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            "f\"ALTER TABLE {daily_basic_table} DELETE WHERE trade_date = '{_TARGET}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {daily_basic_table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            "f\"ALTER TABLE {weekly_table} DELETE WHERE trade_date = '{weekly_period}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {weekly_table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            "f\"ALTER TABLE {monthly_table} DELETE WHERE trade_date = '{monthly_period}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {monthly_table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            "f\"ALTER TABLE {clickhouse.database}.daily_basic DELETE WHERE trade_date = '{_TARGET}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {clickhouse.database}.daily_basic DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        # 回补 flow 测试的清理（2024-01-02~04 区间）
        (
            "f\"ALTER TABLE {table} DELETE WHERE trade_date >= '2024-01-02' \"\n"
            "            \"AND trade_date <= '2024-01-04' SETTINGS mutations_sync = 1\"",
            "f\"ALTER TABLE {table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            "f\"ALTER TABLE {table} DELETE WHERE trade_date >= '2024-01-02' \"\n"
            "            \"AND trade_date <= '2024-01-03' SETTINGS mutations_sync = 1\"",
            "f\"ALTER TABLE {table} DELETE WHERE stock_id LIKE 'it-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
    ],
)

# ---------- test_market_data_capacity.py ----------
patch(
    "tests/integration/test_market_data_capacity.py",
    [
        (
            "def _count(clickhouse: ClickHouseClient, data_kind: DataKind, target: date) -> int:\n"
            "    return MarketDataClickHouseRepository(clickhouse).count(data_kind, target)",
            "def _count(clickhouse: ClickHouseClient, data_kind: DataKind, target: date) -> int:\n"
            "    table = MarketDataClickHouseRepository(clickhouse).TABLE_BY_KIND[data_kind]\n"
            "    rows = clickhouse.execute(\n"
            '        f"SELECT count() AS n FROM {clickhouse.database}.{table} FINAL "\n'
            "        f\"WHERE trade_date = '{target.isoformat()}' AND stock_id LIKE 'cap-%'\"\n"
            "    )\n"
            '    return int(rows[0]["n"])',
        ),
        (
            "f\"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' \"\n"
            '            "SETTINGS mutations_sync = 1"',
            "f\"ALTER TABLE {table} DELETE WHERE stock_id LIKE 'cap-%' \"\n"
            '            "SETTINGS mutations_sync = 1"',
        ),
        (
            'f"ALTER TABLE {clickhouse.database}.weekly_kline "\n'
            "            f\"DELETE WHERE trade_date = '{weekly_period}' SETTINGS mutations_sync = 1\"",
            'f"ALTER TABLE {clickhouse.database}.weekly_kline "\n'
            "            f\"DELETE WHERE stock_id LIKE 'cap-%' SETTINGS mutations_sync = 1\"",
        ),
    ],
)

# ---------- test_market_data_mysql.py（drill 断言与清理）----------
patch(
    "tests/integration/test_market_data_mysql.py",
    [
        # 失败演练断言：按 drill 前缀过滤，避免撞上用户回补数据
        (
            "client.execute(\n"
            "            f\"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}'\"\n"
            '        )[0]["count()"] == 0',
            "client.execute(\n"
            "            f\"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}' \"\n"
            "            \"AND stock_id LIKE 'drill-%'\"\n"
            '        )[0]["count()"] == 0',
        ),
        (
            "client.execute(\n"
            "            f\"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}'\"\n"
            '        )[0]["count()"] == 0',
            "client.execute(\n"
            "            f\"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}' \"\n"
            "            \"AND stock_id LIKE 'drill-%'\"\n"
            '        )[0]["count()"] == 0',
        ),
        (
            "client.execute(\n"
            '            f"SELECT count() FROM {client.database}.adj_factor "\n'
            "            f\"WHERE trade_date = '{target.isoformat()}'\"\n"
            '        )[0]["count()"] == _STOCK_COUNT',
            "client.execute(\n"
            '            f"SELECT count() FROM {client.database}.adj_factor "\n'
            "            f\"WHERE trade_date = '{target.isoformat()}' AND stock_id LIKE 'drill-%'\"\n"
            '        )[0]["count()"] == _STOCK_COUNT',
        ),
        (
            "client.execute(\n"
            "            f\"SELECT count() FROM {basic_table} WHERE trade_date = '{target.isoformat()}'\"\n"
            '        )[0]["count()"] == _STOCK_COUNT',
            "client.execute(\n"
            "            f\"SELECT count() FROM {basic_table} WHERE trade_date = '{target.isoformat()}' \"\n"
            "            \"AND stock_id LIKE 'drill-%'\"\n"
            '        )[0]["count()"] == _STOCK_COUNT',
        ),
        (
            "client.execute(\n"
            "            f\"SELECT count() FROM {daily_table} WHERE trade_date = '{target.isoformat()}'\"\n"
            '        )[0]["count()"] == 0',
            "client.execute(\n"
            "            f\"SELECT count() FROM {daily_table} WHERE trade_date = '{target.isoformat()}' \"\n"
            "            \"AND stock_id LIKE 'drill-%'\"\n"
            '        )[0]["count()"] == 0',
        ),
        # drill 清理：按前缀（已存在）
    ],
)
print("完成")
