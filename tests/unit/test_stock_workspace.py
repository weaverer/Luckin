from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from lucking.services.stock_workspace import QuoteStatus, StockWorkspace


class Stocks:
    def search(self, query, limit, offset, venue_code=None, listing_status=None):
        rows = [SimpleNamespace(stock_id="s1", security_code="600000", name="浦发银行")]
        return rows, 1

    def get(self, stock_id):
        return SimpleNamespace(stock_id=stock_id, security_code="600000", name="浦发银行")


class Quotes:
    def __init__(self, updated_at):
        self.updated_at = updated_at

    def list(self, stock_id, limit, start_date=None, end_date=None):
        return [
            {
                "trade_date": date(2026, 8, 8),
                "close": Decimal("12.3400"),
                "updated_at": self.updated_at,
            }
        ]

    def latest(self, stock_id):
        return self.list(stock_id, 1)[0]


def test_search_returns_stable_page_without_calling_provider() -> None:
    result = StockWorkspace(Stocks(), Quotes(datetime.now(UTC))).search("600", 20, 0)
    assert result.total == 1
    assert result.items[0].security_code == "600000"


def test_quotes_serialize_decimal_as_string_and_report_freshness() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    current = StockWorkspace(Stocks(), Quotes(now), now=lambda: now).quotes("s1", 120)
    stale = StockWorkspace(Stocks(), Quotes(now - timedelta(days=3)), now=lambda: now).quotes(
        "s1", 120
    )
    assert current.status is QuoteStatus.CURRENT
    assert current.items[0]["close"] == "12.3400"
    assert stale.status is QuoteStatus.STALE


def test_missing_quotes_have_explicit_status() -> None:
    class EmptyQuotes:
        def list(self, stock_id, limit, start_date=None, end_date=None):
            return []

        def latest(self, stock_id):
            return None

    result = StockWorkspace(Stocks(), EmptyQuotes()).quotes("s1", 120)
    assert result.status is QuoteStatus.MISSING


def test_clickhouse_string_updated_at_is_parsed_for_freshness() -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    result = StockWorkspace(
        Stocks(),
        Quotes("2026-08-08 11:00:00.000"),
        now=lambda: now,
    ).get("s1")

    assert result is not None
    assert result.market_data_status is QuoteStatus.CURRENT


def test_clickhouse_wire_numbers_are_serialized_as_strings() -> None:
    class WireQuotes:
        def list(self, stock_id, limit, start_date=None, end_date=None):
            return [self.latest(stock_id)]

        def latest(self, stock_id):
            return {
                "stock_id": stock_id,
                "trade_date": "2026-08-08",
                "close": 14.32,
                "vol": 20079,
                "updated_at": "2026-08-08 11:00:00.000",
            }

    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    workspace = StockWorkspace(Stocks(), WireQuotes(), now=lambda: now)

    detail = workspace.get("s1")
    quotes = workspace.quotes("s1")

    assert detail is not None
    assert detail.latest_quote is not None
    assert detail.latest_quote["close"] == "14.32"
    assert quotes.items[0]["vol"] == "20079"
