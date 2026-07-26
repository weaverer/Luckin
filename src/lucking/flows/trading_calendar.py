"""Prefect flow for automatic and manual trading-calendar synchronization."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.runtime import flow_run

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.registry import build_trading_calendar_provider
from lucking.logging import (
    JsonlLogStore,
    calculate_schedule_timing,
    calculate_timeliness_summary,
)
from lucking.ports.trading_calendar_provider import (
    MarketCode,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SyncMode,
)
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository
from lucking.services.trading_calendar import InvalidSyncRequest, SyncResult, TradingCalendarService


class RetryPolicy:
    delays = (30, 120, 300)


def resolve_sync_window(
    mode: SyncMode,
    *,
    as_of_date: date,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[date, date]:
    if mode is SyncMode.MONTHLY:
        if start_date is not None or end_date is not None:
            raise InvalidSyncRequest("计划同步不得传入显式日期")
        return as_of_date.replace(day=1), date(as_of_date.year, 12, 31)
    if mode is SyncMode.YEAR_END:
        if start_date is not None or end_date is not None:
            raise InvalidSyncRequest("计划同步不得传入显式日期")
        target_year = as_of_date.year + 1
        return date(target_year, 1, 1), date(target_year, 12, 31)
    if start_date is None or end_date is None:
        raise InvalidSyncRequest("人工补数必须同时提供开始和结束日期")
    if start_date > end_date:
        raise InvalidSyncRequest("开始日期不得晚于结束日期")
    try:
        maximum_end = start_date.replace(year=start_date.year + 10)
    except ValueError:
        maximum_end = start_date.replace(year=start_date.year + 10, day=28)
    if end_date > maximum_end:
        raise InvalidSyncRequest("人工补数范围不得超过十年")
    return start_date, end_date


def execute_with_retry[T](
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_failure: Callable[[Exception, int], None] | None = None,
) -> T:
    for attempt in range(1, len(RetryPolicy.delays) + 2):
        try:
            return operation()
        except (ProviderRateLimitedError, ProviderUnavailableError) as exc:
            if on_failure is not None:
                on_failure(exc, attempt)
            if attempt > len(RetryPolicy.delays):
                raise
            sleep(RetryPolicy.delays[attempt - 1])
    raise AssertionError("不可达")


def _build_service(settings: Settings) -> TradingCalendarService:
    provider = build_trading_calendar_provider(settings.trading_calendar_provider, settings)
    engine = create_database_engine(settings)
    repository = SqlAlchemyTradingCalendarRepository(create_session_factory(engine))
    return TradingCalendarService(provider, repository)


@flow(name="trading-calendar-sync", log_prints=False)
def sync_trading_calendar(
    mode: str,
    market_code: str = "CN-S",
    start_date: date | None = None,
    end_date: date | None = None,
    as_of_date: date | None = None,
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    settings = Settings()
    sync_mode = SyncMode(mode)
    market = MarketCode.enabled(market_code)
    business_date = as_of_date or datetime.now(
        ZoneInfo(settings.trading_calendar_timezone)
    ).date()
    window_start, window_end = resolve_sync_window(
        sync_mode,
        as_of_date=business_date,
        start_date=start_date,
        end_date=end_date,
    )
    if sync_mode is SyncMode.MANUAL:
        scheduled_at = None
        schedule_slug = None
    elif scheduled_at is None:
        scheduled_at = flow_run.scheduled_start_time
    started_at = datetime.now(UTC)
    log_store = JsonlLogStore(settings.trading_calendar_log_dir)
    run_id = str(flow_run.id or "local")
    service = _build_service(settings)
    common: dict[str, Any] = {
        "flow_run_id": run_id,
        "schedule_slug": schedule_slug,
        "source": service.provider_code,
        "sync_mode": sync_mode,
        "market_code": market,
        "start_date": window_start,
        "end_date": window_end,
        "scheduled_at": scheduled_at,
        "started_at": started_at,
    }
    log_store.write("sync_started", **common)
    attempts = 0

    def operation() -> SyncResult:
        nonlocal attempts
        attempts += 1
        log_store.write("fetch_attempt_started", attempt=attempts, **common)
        return service.sync_range(
            sync_mode, market, window_start, window_end, business_date
        )

    def failed_attempt(error: Exception, attempt: int) -> None:
        log_store.write(
            "fetch_attempt_failed",
            level="WARNING",
            attempt=attempt,
            error_category=getattr(error, "category", type(error).__name__),
            error_summary=str(error),
            **common,
        )

    try:
        result = execute_with_retry(operation, on_failure=failed_attempt)
    except Exception as exc:
        completed_at = datetime.now(UTC)
        timing = calculate_schedule_timing(scheduled_at, started_at, completed_at)
        log_store.write(
            "sync_failed",
            level="ERROR",
            completed_at=completed_at,
            attempt=attempts,
            error_category=getattr(exc, "category", type(exc).__name__),
            error_summary=str(exc),
            **asdict(timing),
            **common,
        )
        raise

    completed_at = datetime.now(UTC)
    timing = calculate_schedule_timing(scheduled_at, started_at, completed_at)
    result_data: dict[str, Any] = {
        "source": result.source,
        "sync_mode": result.sync_mode.value,
        "market_code": result.market_code.value,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "coverage_end": result.coverage_end.isoformat(),
        "completeness_status": result.completeness_status.value,
        "missing_future_count": result.missing_future_count,
        "received_count": result.received_count,
        "written_count": result.written_count,
        "status": "SUCCEEDED",
    }
    terminal_fields = {
        **common,
        **result_data,
        **asdict(timing),
        "completed_at": completed_at,
        "attempt": attempts,
    }
    if schedule_slug:
        summary = calculate_timeliness_summary(
            [*log_store.read_events(), terminal_fields], schedule_slug
        )
        terminal_fields.update(
            {
                "timeliness_sample_size": summary.sample_size,
                "timeliness_met_count": summary.met_count,
                "timeliness_rate": summary.rate,
                "timeliness_formal": summary.formal,
            }
        )
    log_store.write("sync_succeeded", **terminal_fields)
    return result_data
