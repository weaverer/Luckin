from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from lucking.models.market_data import DataKind
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.workbench_queries.j_gold import JGoldQueryRepository


class FakeClickHouse:
    database = "lucking"

    def __init__(self) -> None:
        self.sql = ""

    def execute(self, sql: str):
        self.sql = sql
        return ()


def test_daily_quotes_are_scoped_by_stock_and_stably_sorted() -> None:
    client = FakeClickHouse()
    MarketDataClickHouseRepository(client).query(
        DataKind.DAILY_QUOTE, stock_id="stock-1", limit=120
    )
    assert "stock_id = 'stock-1'" in client.sql
    assert "ORDER BY trade_date, stock_id" in client.sql
    assert "LIMIT 120" in client.sql


def test_public_daily_quote_range_is_capped_at_400_rows() -> None:
    repository = MarketDataClickHouseRepository(FakeClickHouse())
    repository.query(DataKind.DAILY_QUOTE, stock_id="stock-1", limit=400)
    with pytest.raises(ValueError):
        repository.query(DataKind.DAILY_QUOTE, stock_id="stock-1", limit=401)


def test_suspension_gaps_are_not_filled_with_fake_rows() -> None:
    class Rows(FakeClickHouse):
        def execute(self, sql: str):
            self.sql = sql
            return (
                {"trade_date": date(2026, 8, 1), "stock_id": "stock-1"},
                {"trade_date": date(2026, 8, 4), "stock_id": "stock-1"},
            )

    rows = MarketDataClickHouseRepository(Rows()).query(
        DataKind.DAILY_QUOTE, stock_id="stock-1", limit=120
    )
    assert [row["trade_date"] for row in rows] == [date(2026, 8, 1), date(2026, 8, 4)]


def test_post_adjusted_quote_query_uses_clickhouse_compatible_final_subqueries() -> None:
    class Rows(FakeClickHouse):
        def execute(self, sql: str):
            self.sql = sql
            return (
                {
                    "trade_date": "2026-08-07",
                    "stock_id": "stock-1",
                    "venue_code": "XSHE",
                    "security_code": "000001",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "pre_close": 10,
                    "change": 0.5,
                    "pct_chg": 5,
                    "vol": 100,
                    "amount": 1000,
                    "updated_at": "2026-08-08T03:02:07.580000",
                },
            )

    client = Rows()
    rows = MarketDataClickHouseRepository(client).query_daily_quotes_post_adjusted(
        stock_id="stock-1", limit=80
    )
    assert "FINAL AS" not in client.sql
    assert "AS trade_date" in client.sql
    assert rows[0]["trade_date"] == "2026-08-07"


def test_j_gold_batch_quotes_keep_stable_output_names_and_one_query() -> None:
    stock_id = str(uuid4())

    class Rows(FakeClickHouse):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def execute(self, sql: str):
            self.calls += 1
            self.sql = sql
            return (
                {
                    "stock_id": stock_id,
                    "trade_date": "2026-08-07",
                    "close": 10.5,
                    "updated_at": datetime(2026, 8, 8, tzinfo=UTC).isoformat(),
                },
            )

    client = Rows()
    repository = JGoldQueryRepository(
        cast(Any, None),
        cast(Any, None),
        cast(Any, client),
    )
    quotes = repository.stock_quotes_batch([stock_id], 80)
    assert client.calls == 1
    assert "FINAL AS" not in client.sql
    assert "q.stock_id AS stock_id" in client.sql
    assert quotes[stock_id][0].trade_date == date(2026, 8, 7)
