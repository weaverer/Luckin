"""Provider-free read adapters for stocks and daily quotes."""

from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import DataKind
from lucking.models.stock_list import StockCurrent
from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.stock_list import StockListItem


def _stock_item(row: StockCurrent) -> StockListItem:
    return StockListItem(
        stock_id=row.stock_id,
        market_code=row.market_code,
        venue_code=VenueCode(row.venue_code),
        security_code=row.security_code,
        display_name=row.display_name,
        currency_code=row.currency_code,
        listing_status=ListingStatus(row.listing_status),
        listed_on=row.listed_on,
        delisted_on=row.delisted_on,
    )


class StockQueryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def search(
        self,
        query: str,
        limit: int,
        offset: int,
        venue_code: str | None = None,
        listing_status: str | None = None,
    ) -> tuple[list[StockListItem], int]:
        filters = [StockCurrent.market_code == "CN-S"]
        if query:
            filters.append(
                or_(
                    StockCurrent.security_code.startswith(query),
                    StockCurrent.display_name.contains(query),
                )
            )
        if venue_code:
            filters.append(StockCurrent.venue_code == venue_code)
        if listing_status:
            filters.append(StockCurrent.listing_status == listing_status)
        statement = (
            select(StockCurrent)
            .where(*filters)
            .order_by(
                StockCurrent.venue_code,
                StockCurrent.security_code,
                StockCurrent.stock_id,
            )
            .limit(limit)
            .offset(offset)
        )
        with self._sessions() as session:
            total = int(
                session.scalar(select(func.count()).select_from(StockCurrent).where(*filters)) or 0
            )
            rows = list(session.scalars(statement))
        return [_stock_item(row) for row in rows], total

    def get(self, stock_id: str) -> StockListItem | None:
        with self._sessions() as session:
            row = session.get(StockCurrent, stock_id)
            return _stock_item(row) if row is not None else None


class DailyQuoteQueryRepository:
    def __init__(self, quotes: MarketDataClickHouseRepository) -> None:
        self._quotes = quotes

    def list(
        self,
        stock_id: str,
        limit: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._quotes.query_daily_quotes_post_adjusted(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            descending=True,
        )]
        return list(reversed(rows))

    def latest(self, stock_id: str) -> dict[str, Any] | None:
        rows = self._quotes.query_daily_quotes_post_adjusted(
            stock_id=stock_id,
            limit=1,
            descending=True,
        )
        return dict(rows[0]) if rows else None
