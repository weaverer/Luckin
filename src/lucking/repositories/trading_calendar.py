"""Trading-calendar repository port and SQLAlchemy implementation."""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.trading_calendar_provider import ProviderCalendarDay, SyncMode


class CalendarPersistenceError(RuntimeError):
    """A batch could not be committed."""


class TradingCalendarRepository(Protocol):
    def upsert_batch(
        self,
        days: Sequence[ProviderCalendarDay],
        sync_mode: SyncMode,
        written_at: datetime,
    ) -> int: ...

    def get(self, market_code: str, calendar_date: date) -> TradingCalendar | None: ...

    def list_range(
        self, market_code: str, start_date: date, end_date: date
    ) -> list[TradingCalendar]: ...


class SqlAlchemyTradingCalendarRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def upsert_batch(
        self,
        days: Sequence[ProviderCalendarDay],
        sync_mode: SyncMode,
        written_at: datetime,
    ) -> int:
        values = [
            {
                "market_code": day.market_code.value,
                "calendar_date": day.calendar_date,
                "is_open": day.is_open,
                "previous_open_date": day.previous_open_date,
                "source": day.source,
                "source_market": day.source_market,
                "sync_mode": sync_mode.value,
                "created_at": written_at,
                "updated_at": written_at,
            }
            for day in days
        ]
        if not values:
            return 0
        session = self._session_factory()
        try:
            with session.begin():
                dialect = session.get_bind().dialect.name
                if dialect == "mysql":
                    mysql_statement = mysql_insert(TradingCalendar).values(values)
                    mysql_statement = mysql_statement.on_duplicate_key_update(
                        is_open=mysql_statement.inserted.is_open,
                        previous_open_date=mysql_statement.inserted.previous_open_date,
                        source=mysql_statement.inserted.source,
                        source_market=mysql_statement.inserted.source_market,
                        sync_mode=mysql_statement.inserted.sync_mode,
                        updated_at=mysql_statement.inserted.updated_at,
                    )
                    session.execute(mysql_statement)
                elif dialect == "sqlite":
                    sqlite_statement = sqlite_insert(TradingCalendar).values(values)
                    sqlite_statement = sqlite_statement.on_conflict_do_update(
                        index_elements=["market_code", "calendar_date"],
                        set_={
                            "is_open": sqlite_statement.excluded.is_open,
                            "previous_open_date": sqlite_statement.excluded.previous_open_date,
                            "source": sqlite_statement.excluded.source,
                            "source_market": sqlite_statement.excluded.source_market,
                            "sync_mode": sqlite_statement.excluded.sync_mode,
                            "updated_at": sqlite_statement.excluded.updated_at,
                        },
                    )
                    session.execute(sqlite_statement)
                else:
                    raise CalendarPersistenceError(f"不支持的数据库方言：{dialect}")
            return len(values)
        except CalendarPersistenceError:
            raise
        except Exception as exc:
            session.rollback()
            raise CalendarPersistenceError("交易日历批次写入失败") from exc
        finally:
            session.close()

    def get(self, market_code: str, calendar_date: date) -> TradingCalendar | None:
        with self._session_factory() as session:
            return session.get(TradingCalendar, (market_code, calendar_date))

    def list_range(
        self, market_code: str, start_date: date, end_date: date
    ) -> list[TradingCalendar]:
        statement = (
            select(TradingCalendar)
            .where(
                TradingCalendar.market_code == market_code,
                TradingCalendar.calendar_date >= start_date,
                TradingCalendar.calendar_date <= end_date,
            )
            .order_by(TradingCalendar.calendar_date)
        )
        with self._session_factory() as session:
            return list(session.scalars(statement))
