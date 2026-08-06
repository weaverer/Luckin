"""校准 stock_factor 表列：ma_mass 变体命名修复（ma_bfq_mass → ma_mass_bfq）。

2026-08-05 实测发现白名单生成 bug：ma_mass 被误判为 ma 前缀周期组，
生成了不存在的 ma_bfq_mass 等 3 列；来源真实命名为后缀式 ma_mass_bfq 等。
幂等：列已存在/已删除时跳过。
"""

from __future__ import annotations

from lucking.clickhouse import build_clickhouse_client, ClickHousePersistenceError
from lucking.config import Settings

_WRONG = ("ma_bfq_mass", "ma_hfq_mass", "ma_qfq_mass")
_RIGHT = (
    ("ma_mass_bfq", "梅斯线（不复权）"),
    ("ma_mass_hfq", "梅斯线（后复权）"),
    ("ma_mass_qfq", "梅斯线（前复权）"),
)
# 股本/市值类宽精度修正（Decimal(12,4) → Decimal(24,4)，与 005 daily_basic 一致；
# 实测 2026-08-05 回补报 "Decimal value is too big"）。
_WIDE_TYPES = {
    "total_share": "总股本（万股）",
    "float_share": "流通股本（万股）",
    "free_share": "自由流通股本（万股）",
    "total_mv": "总市值（万元）",
    "circ_mv": "流通市值（万元）",
}


def main() -> None:
    settings = Settings()
    client = build_clickhouse_client(settings)
    # 注意：describe_table/execute_ddl 内部自动加数据库前缀，这里传裸表名
    table = "stock_factor"
    existing = {column.name for column in client.describe_table(table)}
    for name in _WRONG:
        if name in existing:
            client.execute_ddl(f"ALTER TABLE {table} DROP COLUMN {name}")
            print(f"dropped {name}")
    for name, comment in _RIGHT:
        if name not in existing:
            client.execute_ddl(
                f"ALTER TABLE {table} ADD COLUMN {name} Nullable(Decimal(12,4)) "
                f"COMMENT '{comment}'"
            )
            print(f"added {name}")
    for name, comment in _WIDE_TYPES.items():
        client.execute_ddl(
            f"ALTER TABLE {table} MODIFY COLUMN {name} Nullable(Decimal(24,4)) "
            f"COMMENT '{comment}'"
        )
        print(f"modified {name} -> Decimal(24,4)")
    columns = {column.name for column in client.describe_table(table)}
    print("columns:", len(columns))
    print("has ma_mass_bfq:", "ma_mass_bfq" in columns)
    print("has ma_bfq_mass:", "ma_bfq_mass" in columns)


if __name__ == "__main__":
    main()
