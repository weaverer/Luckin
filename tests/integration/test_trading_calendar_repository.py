from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, inspect

from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.trading_calendar_provider import MarketCode, ProviderCalendarDay, SyncMode
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository


def _day(calendar_date: date, is_open: bool = True) -> ProviderCalendarDay:
    return ProviderCalendarDay(
        MarketCode.CN_STOCK,
        calendar_date,
        is_open,
        None,
        "memory",
        "TEST",
    )


def test_schema_has_composite_primary_key(sqlite_session_factory) -> None:
    engine = sqlite_session_factory.kw["bind"]
    primary_key = inspect(engine).get_pk_constraint("trading_calendar")
    assert primary_key["constrained_columns"] == ["market_code", "calendar_date"]


def test_upsert_preserves_created_at_and_updates_sync_metadata(sqlite_session_factory) -> None:
    repository = SqlAlchemyTradingCalendarRepository(sqlite_session_factory)
    first = datetime(2026, 7, 1, tzinfo=UTC)
    second = first + timedelta(days=1)

    assert repository.upsert_batch([_day(date(2026, 7, 1))], SyncMode.MONTHLY, first) == 1
    repository.upsert_batch(
        [_day(date(2026, 7, 1), is_open=False)], SyncMode.MANUAL, second
    )
    row = repository.get("CN-S", date(2026, 7, 1))

    assert isinstance(row, TradingCalendar)
    assert row.created_at == first.replace(tzinfo=None)
    assert row.updated_at == second.replace(tzinfo=None)
    assert row.sync_mode == "manual"
    assert row.is_open is False


@pytest.mark.mysql
def test_mysql_upsert_is_atomic_and_preserves_created_at(mysql_session_factory) -> None:
    repository = SqlAlchemyTradingCalendarRepository(mysql_session_factory)
    target_date = date(2099, 12, 30)
    first = datetime(2026, 7, 1, tzinfo=UTC)
    second = first + timedelta(days=1)
    try:
        repository.upsert_batch([_day(target_date)], SyncMode.MONTHLY, first)
        repository.upsert_batch([_day(target_date, False)], SyncMode.YEAR_END, second)
        row = repository.get("CN-S", target_date)
        assert row is not None
        assert row.created_at == first.replace(tzinfo=None)
        assert row.updated_at == second.replace(tzinfo=None)
        assert row.sync_mode == "year_end"
        assert row.is_open is False
    finally:
        with mysql_session_factory.begin() as session:
            session.execute(
                delete(TradingCalendar).where(
                    TradingCalendar.market_code == "CN-S",
                    TradingCalendar.calendar_date == target_date,
                )
            )

