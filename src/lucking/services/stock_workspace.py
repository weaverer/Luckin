"""Stock search, detail and quote freshness domain service."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from lucking.repositories.stock_list import StockListItem

_QUOTE_DECIMAL_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
}


class QuoteStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    MISSING = "MISSING"


class Stocks(Protocol):
    def search(
        self,
        query: str,
        limit: int,
        offset: int,
        venue_code: str | None = None,
        listing_status: str | None = None,
    ) -> tuple[list[StockListItem], int]: ...

    def get(self, stock_id: str) -> StockListItem | None: ...


class Quotes(Protocol):
    def list(
        self,
        stock_id: str,
        limit: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]: ...

    def latest(self, stock_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class StockPage:
    items: list[StockListItem]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class QuoteResult:
    items: list[dict[str, Any]]
    status: QuoteStatus


@dataclass(frozen=True, slots=True)
class StockDetail:
    stock: StockListItem
    latest_quote: dict[str, Any] | None
    market_data_status: QuoteStatus


class StockWorkspace:
    def __init__(
        self, stocks: Stocks, quotes: Quotes, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self._stocks = stocks
        self._quotes = quotes
        self._now = now or (lambda: datetime.now(UTC))

    def search(
        self,
        query: str,
        limit: int,
        offset: int,
        venue_code: str | None = None,
        listing_status: str | None = None,
    ) -> StockPage:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("分页参数非法")
        items, total = self._stocks.search(query.strip(), limit, offset, venue_code, listing_status)
        return StockPage(items, total, limit, offset)

    def get(self, stock_id: str) -> StockDetail | None:
        stock = self._stocks.get(stock_id)
        if stock is None:
            return None
        latest = self._quotes.latest(stock_id)
        if latest is None:
            return StockDetail(stock, None, QuoteStatus.MISSING)
        converted = _serialize_quote(latest)
        return StockDetail(stock, converted, self._quote_status([latest]))

    def quotes(
        self,
        stock_id: str,
        limit: int = 120,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> QuoteResult:
        if not 1 <= limit <= 400:
            raise ValueError("行情范围必须为 1 至 400 条")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        rows = self._quotes.list(stock_id, limit, start_date, end_date)
        if not rows:
            return QuoteResult([], QuoteStatus.MISSING)
        converted = [_serialize_quote(row) for row in rows]
        return QuoteResult(converted, self._quote_status(rows))

    def _quote_status(self, rows: list[dict[str, Any]]) -> QuoteStatus:
        updated = max(_as_datetime(row["updated_at"]) for row in rows)
        return (
            QuoteStatus.STALE if self._now() - updated > timedelta(days=1) else QuoteStatus.CURRENT
        )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("行情更新时间格式无效") from exc
    else:
        raise ValueError("行情更新时间格式无效")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _serialize_quote(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value)
        if key in _QUOTE_DECIMAL_FIELDS and isinstance(value, (Decimal, int, float))
        else value
        for key, value in row.items()
    }
