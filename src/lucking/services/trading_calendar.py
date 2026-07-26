"""Trading calendar domain contracts and synchronization service."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from lucking.ports.trading_calendar_provider import (
    MarketCode,
    ProviderCalendarDay,
    SyncMode,
    TradingCalendarProvider,
)
from lucking.repositories.trading_calendar import TradingCalendarRepository


class InvalidSyncRequest(ValueError):
    pass


class InvalidCalendarPayload(ValueError):
    pass


class CalendarStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class CompletenessStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FUTURE_PARTIAL = "FUTURE_PARTIAL"


@dataclass(frozen=True, slots=True)
class CalendarQueryResult:
    market_code: MarketCode
    calendar_date: date
    status: CalendarStatus
    sync_mode: SyncMode | None = None


@dataclass(frozen=True, slots=True)
class SyncResult:
    source: str
    sync_mode: SyncMode
    market_code: MarketCode
    start_date: date
    end_date: date
    coverage_end: date
    completeness_status: CompletenessStatus
    missing_future_count: int
    received_count: int
    written_count: int


class TradingCalendarService:
    def __init__(
        self,
        provider: TradingCalendarProvider,
        repository: TradingCalendarRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def provider_code(self) -> str:
        return self._provider.provider_code

    def sync_range(
        self,
        sync_mode: SyncMode,
        market_code: MarketCode,
        start_date: date,
        end_date: date,
        as_of_date: date,
    ) -> SyncResult:
        self._validate_request(market_code, start_date, end_date)
        received = self._provider.fetch_calendar(market_code, start_date, end_date)
        ordered, completeness, coverage_end = self._validate_payload(
            received,
            market_code,
            start_date,
            end_date,
            as_of_date,
            self._provider.provider_code,
        )
        written_at = self._now()
        written = self._repository.upsert_batch(ordered, sync_mode, written_at)
        return SyncResult(
            source=self._provider.provider_code,
            sync_mode=sync_mode,
            market_code=market_code,
            start_date=start_date,
            end_date=end_date,
            coverage_end=coverage_end,
            completeness_status=completeness,
            missing_future_count=(end_date - coverage_end).days,
            received_count=len(received),
            written_count=written,
        )

    def get_status(self, market_code: MarketCode, calendar_date: date) -> CalendarQueryResult:
        MarketCode.enabled(market_code)
        row = self._repository.get(market_code.value, calendar_date)
        if row is None:
            return CalendarQueryResult(market_code, calendar_date, CalendarStatus.UNKNOWN)
        return CalendarQueryResult(
            market_code,
            calendar_date,
            CalendarStatus.OPEN if row.is_open else CalendarStatus.CLOSED,
            SyncMode(row.sync_mode),
        )

    def list_range(
        self, market_code: MarketCode, start_date: date, end_date: date
    ) -> list[CalendarQueryResult]:
        self._validate_request(market_code, start_date, end_date)
        return [
            CalendarQueryResult(
                market_code,
                row.calendar_date,
                CalendarStatus.OPEN if row.is_open else CalendarStatus.CLOSED,
                SyncMode(row.sync_mode),
            )
            for row in self._repository.list_range(market_code.value, start_date, end_date)
        ]

    @staticmethod
    def _validate_request(market_code: MarketCode, start_date: date, end_date: date) -> None:
        try:
            MarketCode.enabled(market_code)
        except Exception as exc:
            raise InvalidSyncRequest(str(exc)) from exc
        if start_date > end_date:
            raise InvalidSyncRequest("开始日期不得晚于结束日期")
        if end_date > _add_years(start_date, 10):
            raise InvalidSyncRequest("同步范围不得超过十年")

    @staticmethod
    def _validate_payload(
        days: list[ProviderCalendarDay],
        market_code: MarketCode,
        start_date: date,
        end_date: date,
        as_of_date: date,
        provider_code: str,
    ) -> tuple[list[ProviderCalendarDay], CompletenessStatus, date]:
        if not days:
            raise InvalidCalendarPayload("Provider 返回空批次")
        by_date: dict[date, ProviderCalendarDay] = {}
        for day in days:
            if day.calendar_date in by_date:
                raise InvalidCalendarPayload("批次包含重复日期")
            if day.market_code != market_code:
                raise InvalidCalendarPayload("批次市场代码不一致")
            if not start_date <= day.calendar_date <= end_date:
                raise InvalidCalendarPayload("批次包含越界日期")
            if day.source != provider_code or not day.source_market:
                raise InvalidCalendarPayload("来源标识不能为空")
            if day.previous_open_date is not None and day.previous_open_date >= day.calendar_date:
                raise InvalidCalendarPayload("上一交易日必须早于日历日期")
            by_date[day.calendar_date] = day

        ordered = [by_date[key] for key in sorted(by_date)]
        coverage_end = ordered[-1].calendar_date
        required_end = min(end_date, as_of_date)
        required_start = start_date
        if required_start <= required_end:
            expected_required = _date_set(required_start, required_end)
            if not expected_required.issubset(by_date):
                raise InvalidCalendarPayload("历史或当日区间存在缺口")

        expected_prefix = _date_set(start_date, coverage_end)
        if set(by_date) != expected_prefix:
            raise InvalidCalendarPayload("返回区间存在内部断点")
        if coverage_end == end_date:
            return ordered, CompletenessStatus.COMPLETE, coverage_end
        if coverage_end <= as_of_date:
            raise InvalidCalendarPayload("缺失区间包含历史或当日")
        return ordered, CompletenessStatus.FUTURE_PARTIAL, coverage_end


def _date_set(start_date: date, end_date: date) -> set[date]:
    return {
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    }


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)
