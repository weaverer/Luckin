"""Read model for the personal trading calendar."""

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from typing import Any, Protocol


class InvalidCalendarRange(ValueError):
    pass


def normalize_important_date_title(value: str) -> tuple[str, str]:
    """Return the display title and its stable uniqueness key."""
    display = " ".join(value.split())
    if not display:
        raise ValueError("重要日标题不能为空")
    return display, display.casefold()


@dataclass(frozen=True, slots=True)
class CalendarDay:
    date: Date
    market_code: str
    market_status: str
    previous_open_date: Date | None
    calendar_updated_at: datetime | None
    important_dates: list[Any]


class CalendarRows(Protocol):
    def list_range(self, market_code: str, start_date: Date, end_date: Date) -> list[Any]: ...


class ImportantRows(Protocol):
    def list_range(self, user_id: str, start_date: Date, end_date: Date) -> list[Any]: ...


class CalendarWorkspace:
    def __init__(self, calendars: CalendarRows, important_dates: ImportantRows) -> None:
        self._calendars = calendars
        self._important_dates = important_dates

    def list_calendar(
        self, user_id: str, start_date: Date, end_date: Date, market_code: str = "CN-S"
    ) -> list[CalendarDay]:
        if end_date < start_date or (end_date - start_date).days >= 400:
            raise InvalidCalendarRange("日期范围必须为 1 至 400 天")
        calendar = {
            row.calendar_date: row
            for row in self._calendars.list_range(market_code, start_date, end_date)
        }
        important: dict[Date, list[Any]] = {}
        for item in self._important_dates.list_range(user_id, start_date, end_date):
            important.setdefault(item.event_date, []).append(item)
        result: list[CalendarDay] = []
        current = start_date
        while current <= end_date:
            row = calendar.get(current)
            result.append(
                CalendarDay(
                    current,
                    market_code,
                    "UNKNOWN" if row is None else ("OPEN" if row.is_open else "CLOSED"),
                    None if row is None else row.previous_open_date,
                    None if row is None else row.updated_at,
                    important.get(current, []),
                )
            )
            current += timedelta(days=1)
        return result
