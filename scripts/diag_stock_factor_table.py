"""诊断 stock_factor 表状态：describe 失败原因与影响范围。"""

from __future__ import annotations

from lucking.clickhouse import build_clickhouse_client
from lucking.config import Settings


def main() -> None:
    settings = Settings()
    client = build_clickhouse_client(settings)

    print("--- system.tables 元数据 ---")
    rows = client.execute(
        "SELECT name, engine, partition_key, sorting_key, total_rows "
        "FROM system.tables WHERE database = 'lucking' AND name = 'stock_factor'"
    )
    print(rows)

    print("--- describe stock_factor（原始执行） ---")
    try:
        rows = client.execute("DESCRIBE TABLE lucking.stock_factor")
        print("ok, columns:", len(rows))
    except Exception as exc:
        print(f"DESCRIBE 失败: {type(exc).__name__}: {getattr(exc, 'summary', exc)}")

    print("--- describe 其他表（对照组） ---")
    try:
        rows = client.execute("DESCRIBE TABLE lucking.index_factor")
        print("index_factor ok, columns:", len(rows))
    except Exception as exc:
        print(f"index_factor DESCRIBE 失败: {getattr(exc, 'summary', exc)}")

    print("--- SELECT count（探测可用性） ---")
    try:
        rows = client.execute(
            "SELECT count() AS count FROM lucking.stock_factor FINAL"
        )
        print("ok:", rows)
    except Exception as exc:
        print(f"SELECT 失败: {getattr(exc, 'summary', exc)}")


if __name__ == "__main__":
    main()
