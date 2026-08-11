from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.repositories.stock_list import StockListItem
from lucking.services.stock_workspace import StockWorkspace


class TenThousandStocks:
    def __init__(self) -> None:
        self.rows = [
            StockListItem(
                f"stock-{index}",
                "CN-S",
                VenueCode.SHANGHAI,
                f"{index:06d}",
                f"测试股票{index:05d}",
                "CNY",
                ListingStatus.ACTIVE,
                None,
                None,
            )
            for index in range(10_000)
        ]

    def search(self, query, limit, offset, venue_code=None, listing_status=None):
        matches = [
            item
            for item in self.rows
            if not query or query in item.security_code or query in item.display_name
        ]
        return matches[offset : offset + limit], len(matches)

    def get(self, stock_id):
        return next((row for row in self.rows if row.stock_id == stock_id), None)


class FourHundredQuotes:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.rows = [
            {
                "trade_date": date(2026, 8, 8) - timedelta(days=index),
                "close": Decimal("12.3400"),
                "updated_at": now,
            }
            for index in range(400)
        ]

    def list(self, stock_id, limit, start_date=None, end_date=None):
        return self.rows[:limit]

    def latest(self, stock_id):
        return self.rows[0]


def test_ten_thousand_stock_paging_and_search_stay_below_two_seconds() -> None:
    workspace = StockWorkspace(TenThousandStocks(), FourHundredQuotes())
    started = perf_counter()
    page = workspace.search("测试股票099", 50, 0)
    elapsed = perf_counter() - started

    assert page.total == 100
    assert len(page.items) == 50
    assert elapsed < 2.0


def test_four_hundred_daily_quotes_are_returned_within_budget() -> None:
    workspace = StockWorkspace(TenThousandStocks(), FourHundredQuotes())
    started = perf_counter()
    result = workspace.quotes("stock-1", 400)
    elapsed = perf_counter() - started

    assert len(result.items) == 400
    assert all(item["close"] == "12.3400" for item in result.items)
    assert elapsed < 2.0
