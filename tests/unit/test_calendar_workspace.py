from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from lucking.services.calendar_workspace import (
    CalendarWorkspace,
    InvalidCalendarRange,
    normalize_important_date_title,
)


class Calendars:
    def list_range(self, market_code, start_date, end_date):
        return [
            SimpleNamespace(
                calendar_date=start_date,
                is_open=True,
                previous_open_date=None,
                updated_at=datetime.now(UTC),
            )
        ]


class Dates:
    seen_user_id = ""

    def list_range(self, user_id, start_date, end_date):
        self.seen_user_id = user_id
        return []


def test_missing_days_are_unknown():
    result = CalendarWorkspace(Calendars(), Dates()).list_calendar(
        "u1", date(2026, 8, 1), date(2026, 8, 2)
    )
    assert [day.market_status for day in result] == ["OPEN", "UNKNOWN"]


def test_range_is_limited_to_400_days():
    with pytest.raises(InvalidCalendarRange):
        CalendarWorkspace(Calendars(), Dates()).list_calendar(
            "u1", date(2025, 1, 1), date(2026, 2, 6)
        )


def test_exactly_400_days_is_allowed():
    result = CalendarWorkspace(Calendars(), Dates()).list_calendar(
        "u1", date(2025, 1, 1), date(2026, 2, 4)
    )
    assert len(result) == 400


def test_title_normalization_collapses_whitespace_and_casefolds_key():
    assert normalize_important_date_title("  财报   发布  ") == ("财报 发布", "财报 发布")
    assert normalize_important_date_title("  Earnings  DAY ") == (
        "Earnings DAY",
        "earnings day",
    )


def test_calendar_reads_only_the_authenticated_users_dates():
    dates = Dates()
    CalendarWorkspace(Calendars(), dates).list_calendar(
        "owner-a", date(2026, 8, 1), date(2026, 8, 2)
    )
    assert dates.seen_user_id == "owner-a"
