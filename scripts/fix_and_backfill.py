"""临时脚本：清理测试残留 + 补齐真实缺口（幂等，已成功日期自动跳过）。"""

from datetime import date

from lucking.clickhouse import build_clickhouse_client
from lucking.config import Settings
from lucking.flows.market_data import backfill_market_data
from lucking.models.market_data import DataKind

settings = Settings()
client = build_clickhouse_client(settings)

# 1) 清理测试残留（1990 年 marker 数据）
print("=== 清理测试残留 ===")
for table in ("daily_quote", "adj_factor", "daily_basic", "weekly_kline", "monthly_kline"):
    rows = client.execute(
        f"SELECT count() AS n FROM {settings.clickhouse_database}.{table} WHERE trade_date < '2024-01-01'"
    )
    n = rows[0]["n"]
    if n:
        client.execute_ddl(
            f"ALTER TABLE {settings.clickhouse_database}.{table} "
            "DELETE WHERE trade_date < '2024-01-01' SETTINGS mutations_sync = 1"
        )
        print(f"  {table}: 清理 {n} 行测试残留")

# 2) 补齐三类真实缺口（区间全覆盖，幂等跳过已成功）
print("\n=== 补齐回补（2024-01-01 ~ 2026-07-31）===")
for data_kind in (DataKind.DAILY_QUOTE, DataKind.ADJ_FACTOR, DataKind.DAILY_BASIC):
    result = backfill_market_data(
        data_kind,
        start_date=date(2024, 1, 1),
        end_date=date(2026, 7, 31),
        backfill_batch_id="verify-fill-20260802",
    )
    print(
        f"{data_kind.value}: 成功 {result['succeeded_day_count']} 天, "
        f"跳过 {result['skipped_day_count']} 天, 失败 {result['failed_day_count']} 天"
    )
    if result["failed_dates"]:
        print(f"  失败日期: {result['failed_dates']}")
