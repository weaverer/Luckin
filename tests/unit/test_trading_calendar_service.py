from datetime import UTC, date, datetime, timedelta

import pytest

from lucking.ports.trading_calendar_provider import MarketCode, ProviderCalendarDay, SyncMode
from lucking.services.trading_calendar import (
    CalendarStatus,
    CompletenessStatus,
    InvalidCalendarPayload,
    InvalidSyncRequest,
    TradingCalendarService,
)


def _days(start: date, end: date) -> list[ProviderCalendarDay]:
    return [
        ProviderCalendarDay(
            MarketCode.CN_STOCK,
            start + timedelta(days=offset),
            (start + timedelta(days=offset)).weekday() < 5,
            None,
            "memory",
            "TEST",
        )
        for offset in range((end - start).days + 1)
    ]


class Provider:
    provider_code = "memory"

    def __init__(self, rows):
        self.rows = rows

    def fetch_calendar(self, market_code, start_date, end_date):
        return list(self.rows)


class Repository:
    def __init__(self):
        self.rows = {}
        self.writes = 0

    def upsert_batch(self, days, sync_mode, written_at):
        for day in days:
            self.rows[(day.market_code.value, day.calendar_date)] = type(
                "Row",
                (),
                {
                    "calendar_date": day.calendar_date,
                    "is_open": day.is_open,
                    "sync_mode": sync_mode.value,
                },
            )()
        self.writes += 1
        return len(days)

    def get(self, market_code, calendar_date):
        return self.rows.get((market_code, calendar_date))

    def list_range(self, market_code, start_date, end_date):
        return [
            row
            for (market, day), row in sorted(self.rows.items())
            if market == market_code and start_date <= day <= end_date
        ]


def _service(rows, repository=None):
    repository = repository or Repository()
    return (
        TradingCalendarService(
            Provider(rows),
            repository,
            now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
        ),
        repository,
    )


def test_complete_and_future_partial_results() -> None:
    start, today, end = date(2026, 7, 24), date(2026, 7, 26), date(2026, 7, 31)
    complete, _ = _service(_days(start, end))
    partial, _ = _service(_days(start, date(2026, 7, 28)))

    assert complete.sync_range(
        SyncMode.MONTHLY, MarketCode.CN_STOCK, start, end, today
    ).completeness_status is CompletenessStatus.COMPLETE
    result = partial.sync_range(SyncMode.MONTHLY, MarketCode.CN_STOCK, start, end, today)
    assert result.completeness_status is CompletenessStatus.FUTURE_PARTIAL
    assert result.coverage_end == date(2026, 7, 28)
    assert result.missing_future_count == 3


@pytest.mark.parametrize(
    "rows",
    [
        [],
        _days(date(2026, 7, 24), date(2026, 7, 25)),
        _days(date(2026, 7, 24), date(2026, 7, 28))[:-2]
        + _days(date(2026, 7, 28), date(2026, 7, 28)),
    ],
)
def test_empty_historical_gap_and_internal_gap_never_write(rows) -> None:
    service, repository = _service(rows)
    with pytest.raises(InvalidCalendarPayload):
        service.sync_range(
            SyncMode.MONTHLY,
            MarketCode.CN_STOCK,
            date(2026, 7, 24),
            date(2026, 7, 31),
            date(2026, 7, 26),
        )
    assert repository.writes == 0


def test_status_and_sorted_range_distinguish_unknown() -> None:
    repository = Repository()
    service, _ = _service(_days(date(2026, 7, 24), date(2026, 7, 25)), repository)
    service.sync_range(
        SyncMode.MANUAL,
        MarketCode.CN_STOCK,
        date(2026, 7, 24),
        date(2026, 7, 25),
        date(2026, 7, 25),
    )
    assert service.get_status(MarketCode.CN_STOCK, date(2026, 7, 24)).status in {
        CalendarStatus.OPEN,
        CalendarStatus.CLOSED,
    }
    assert (
        service.get_status(MarketCode.CN_STOCK, date(2026, 7, 26)).status
        is CalendarStatus.UNKNOWN
    )
    result = service.list_range(
        MarketCode.CN_STOCK, date(2026, 7, 24), date(2026, 7, 26)
    )
    assert [row.calendar_date for row in result] == sorted(row.calendar_date for row in result)


def test_reverse_and_over_ten_year_ranges_are_rejected() -> None:
    service, _ = _service([])
    with pytest.raises(InvalidSyncRequest):
        service.list_range(
            MarketCode.CN_STOCK, date(2026, 7, 2), date(2026, 7, 1)
        )
    with pytest.raises(InvalidSyncRequest):
        service.list_range(
            MarketCode.CN_STOCK, date(2020, 1, 1), date(2030, 1, 2)
        )

