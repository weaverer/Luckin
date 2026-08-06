"""Prefect 工作流：行情数据交易日计划同步（参数化 data_kind）。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from prefect import flow
from prefect.runtime import flow_run

from lucking.clickhouse import build_clickhouse_client
from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.registry import (
    build_adj_factor_provider,
    build_daily_basic_provider,
    build_daily_quote_provider,
    build_kline_provider,
)
from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.models.market_data import DataKind
from lucking.repositories.market_data import (
    BackfillDateAction,
    SqlAlchemyMarketDataRepository,
)
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository
from lucking.services.market_data import (
    BackfillMarketDataCommand,
    MarketDataProvider,
    MarketDataService,
    MarketDataSyncResult,
    RetryMarketDataSyncCommand,
    ScheduledMarketDataSyncCommand,
    SyncStatus,
)


def _build_service(settings: Settings) -> MarketDataService:
    providers: dict[DataKind, MarketDataProvider] = {
        DataKind.DAILY_QUOTE: build_daily_quote_provider(
            settings.daily_quote_provider, settings
        ),
        DataKind.ADJ_FACTOR: build_adj_factor_provider(
            settings.adj_factor_provider, settings
        ),
        DataKind.DAILY_BASIC: build_daily_basic_provider(
            settings.daily_basic_provider, settings
        ),
        DataKind.WEEKLY_KLINE: build_kline_provider(settings.kline_provider, settings),
        DataKind.MONTHLY_KLINE: build_kline_provider(settings.kline_provider, settings),
    }
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyMarketDataRepository(
        session_factory,
        lease_seconds=settings.market_data_run_lease_seconds,
    )
    clickhouse = MarketDataClickHouseRepository(build_clickhouse_client(settings))
    return MarketDataService(
        providers,
        repository,
        clickhouse,
        session_factory,
        timezone=settings.market_data_timezone,
        fetch_deadline_seconds=settings.market_data_fetch_deadline_seconds,
        page_limit=settings.market_data_page_limit,
    )


@flow(name="market-data-sync", retries=0)
def market_data_sync(
    data_kind: DataKind,
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    settings = Settings()
    actual_scheduled_at = scheduled_at or flow_run.scheduled_start_time
    if actual_scheduled_at is None:
        raise ValueError("直接调用时必须显式提供 scheduled_at")
    if actual_scheduled_at.tzinfo is None:
        raise ValueError("scheduled_at 必须包含时区")
    actual_slug = (schedule_slug or "").strip()
    if not actual_slug:
        raise ValueError("schedule_slug 不能为空")
    flow_run_id = flow_run.id or str(uuid4())
    started_at = datetime.now(UTC)
    logs = _log_store(settings)
    common: dict[str, Any] = {
        "flow_run_id": flow_run_id,
        "data_kind": data_kind.value,
        "schedule_slug": actual_slug,
        "scheduled_at": actual_scheduled_at,
        "run_kind": "SCHEDULED",
        "started_at": started_at,
    }
    logs.write("market_data_sync_started", **common)
    try:
        result = _build_service(settings).sync(
            ScheduledMarketDataSyncCommand(data_kind, actual_slug, actual_scheduled_at, flow_run_id)
        )
    except Exception as exc:
        completed_at = datetime.now(UTC)
        logs.write(
            "market_data_sync_failed",
            level="ERROR",
            **common,
            completed_at=completed_at,
            error_category=getattr(exc, "category", "UNEXPECTED"),
            error_summary=getattr(exc, "summary", "行情数据同步失败"),
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=_window_target_ms(settings, data_kind),
                )
            ),
        )
        raise
    completed_at = datetime.now(UTC)
    payload = _serialize_result(result)
    # common 已携带的键（data_kind/run_kind 等）不再重复传入，避免关键字冲突
    payload = {key: value for key, value in payload.items() if key not in common}
    if result.status is SyncStatus.SKIPPED:
        logs.write(
            "market_data_sync_skipped",
            **common,
            **payload,
            skipped=True,
            completed_at=completed_at,
        )
    else:
        logs.write(
            "market_data_sync_succeeded",
            **common,
            **payload,
            completed_at=completed_at,
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=_window_target_ms(settings, data_kind),
                )
            ),
        )
    return payload


def expand_backfill_dates(
    start_date: date,
    end_date: date,
    *,
    trade_days: set[date],
) -> tuple[date, ...]:
    """把回补区间按交易日展开（端点均计入）。"""
    if start_date > end_date:
        raise ValueError("开始日期不得晚于结束日期")
    return tuple(
        day
        for day in range_days(start_date, end_date)
        if day in trade_days
    )


def range_days(start_date: date, end_date: date) -> list[date]:
    from datetime import timedelta

    days: list[date] = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


@flow(name="market-data-backfill", retries=0)
def backfill_market_data(
    data_kind: DataKind,
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    """历史回补：区间整体校验后按交易日历逐日展开，逐日独立终态。"""
    settings = Settings()
    batch_id = backfill_batch_id.strip()
    if not batch_id:
        raise ValueError("backfill_batch_id 不能为空")
    _validate_backfill_range(start_date, end_date, settings.market_data_timezone)
    service = _build_service(settings)
    trade_days = _load_trade_days(settings, start_date, end_date)
    days = expand_backfill_dates(start_date, end_date, trade_days=trade_days)
    succeeded = failed = skipped = in_progress = 0
    failed_dates: list[str] = []
    for target in days:
        resolution = service.resolve_backfill_date(
            data_kind=data_kind,
            backfill_batch_id=batch_id,
            target_trade_date=target,
        )
        if resolution.action is BackfillDateAction.SKIP_SUCCEEDED:
            skipped += 1
            continue
        if resolution.action is BackfillDateAction.IN_PROGRESS:
            in_progress += 1
            continue
        flow_run_id = flow_run.id or str(uuid4())
        command = (
            RetryMarketDataSyncCommand(
                resolution.run_id or "", f"{flow_run_id}:{target}:retry"
            )
            if resolution.action is BackfillDateAction.RETRY
            else BackfillMarketDataCommand(
                data_kind, target, batch_id, f"{flow_run_id}:{target}:backfill"
            )
        )
        try:
            service.sync(command)
            succeeded += 1
        except Exception:
            failed += 1
            failed_dates.append(target.isoformat())
    return {
        "backfill_batch_id": batch_id,
        "data_kind": data_kind.value,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_trade_day_count": len(days),
        "succeeded_day_count": succeeded,
        "failed_day_count": failed,
        "skipped_day_count": skipped,
        "in_progress_day_count": in_progress,
        "failed_dates": failed_dates,
    }


def _validate_backfill_range(start_date: date, end_date: date, timezone: str) -> None:
    from zoneinfo import ZoneInfo

    if start_date > end_date:
        raise ValueError("开始日期不得晚于结束日期")
    if start_date < date(2024, 1, 1):
        raise ValueError("回补不得早于 2024-01-01")
    if end_date > datetime.now(ZoneInfo(timezone)).date():
        raise ValueError("回补不得包含未来交易日")


def _load_trade_days(settings: Settings, start_date: date, end_date: date) -> set[date]:
    engine = create_database_engine(settings)
    try:
        repository = SqlAlchemyTradingCalendarRepository(create_session_factory(engine))
        days = repository.list_range("CN-S", start_date, end_date)
        return {day.calendar_date for day in days if day.is_open}
    finally:
        engine.dispose()


def _window_target_ms(settings: Settings, data_kind: DataKind) -> int:
    """窗口及时性目标：复权因子（09:30 启动，单请求快速收敛）按 20 分钟衡量；
    日线（17:00）等其余数据类要求当日形成终态（按 8 小时窗口衡量）。"""
    if data_kind is DataKind.ADJ_FACTOR:
        return 20 * 60 * 1000
    return 8 * 60 * 60 * 1000


def _serialize_result(result: MarketDataSyncResult) -> dict[str, Any]:
    payload: dict[str, Any] = asdict(result)
    for key, value in tuple(payload.items()):
        if isinstance(value, date):
            payload[key] = value.isoformat()
        elif hasattr(value, "value"):
            payload[key] = value.value
    return payload


def _log_store(settings: Settings) -> JsonlLogStore:
    return JsonlLogStore(
        settings.market_data_log_dir,
        filename=settings.market_data_log_filename,
    )
