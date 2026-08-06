"""验证 Decimal(24,4) 宽精度：写入触发报错的确切值并读回、清理。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from lucking.clickhouse import build_clickhouse_client
from lucking.config import Settings

_SID = "00000000-0000-0000-0000-00000000beef"


def main() -> None:
    settings = Settings()
    client = build_clickhouse_client(settings)
    target = date(2004, 1, 5)
    row = {
        "trade_date": target,
        "stock_id": _SID,
        "stock_code": "TEST.SH",
        "close": Decimal("5.00"),
        "total_mv": Decimal("119545716.605"),  # 触发报错的精确值（工商银行级市值）
        "circ_mv": Decimal("119545716.605"),
        "total_share": Decimal("3564060000.0"),
        "updated_at": datetime(2026, 8, 5, 10, 0, 0, tzinfo=UTC),
    }
    try:
        client.insert_rows("stock_factor", tuple(row.keys()), [row])
        rows = client.execute(
            "SELECT total_mv, circ_mv, total_share FROM lucking.stock_factor FINAL "
            f"WHERE stock_id = '{_SID}'"
        )
        print("stored:", rows)
        assert str(rows[0]["total_mv"]).startswith("119545716")
    finally:
        client.execute_ddl(
            f"ALTER TABLE lucking.stock_factor DELETE WHERE stock_id = '{_SID}' "
            "SETTINGS mutations_sync = 1"
        )
        print("cleanup ok")


if __name__ == "__main__":
    main()
