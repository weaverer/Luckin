"""Prefect 工作流：指数技术因子交易日计划同步与人工回补。"""

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
from lucking.flows.market_data import expand_backfill_dates
from lucking.integrations.registry import build_index_factor_provider
from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.repositories.index_factor_clickhouse import IndexFactorClickHouseRepository
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import (
    BackfillDateAction,
    SqlAlchemyMarketDataRepository,
)
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository
from lucking.services.index_factor import (
    BackfillIndexFactorCommand,
    IndexFactorService,
    IndexFactorSyncResult,
    IndexFactorSyncStatus,
    RetryIndexFactorSyncCommand,
    ScheduledIndexFactorSyncCommand,
)

# 与 market_data 回补起点一致；Clarifications 确认 2024-01-01。
BACKFILL_START = date(2024, 1, 1)
_WINDOW_TARGET_MS = 8 * 60 * 60 * 1000  # 17:00 启动当日形成终态


def _build_service(settings: Settings) -> IndexFactorService:
    provider = build_index_factor_provider(settings.index_factor_provider, settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyMarketDataRepository(
        session_factory,
        lease_seconds=settings.index_factor_run_lease_seconds,
    )
    identity = IndexFactorIdentityRepository(session_factory)
    clickhouse = IndexFactorClickHouseRepository(build_clickhouse_client(settings))
    return IndexFactorService(
        provider,
        repository,
        identity,
        clickhouse,
        session_factory,
        timezone=settings.index_factor_timezone,
        fetch_deadline_seconds=settings.index_factor_fetch_deadline_seconds,
        page_limit=settings.index_factor_page_limit,
    )


@flow(name="index-factor-sync", retries=0)
def index_factor_sync(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    """每个交易日北京时间 17:00 同步全部指数技术因子（spec FR-002）。"""
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
        "schedule_slug": actual_slug,
        "scheduled_at": actual_scheduled_at,
        "run_kind": "SCHEDULED",
        "started_at": started_at,
    }
    logs.write("index_factor_sync_started", **common)
    try:
        result = _build_service(settings).sync(
            ScheduledIndexFactorSyncCommand(actual_slug, actual_scheduled_at, flow_run_id)
        )
    except Exception as exc:
        completed_at = datetime.now(UTC)
        logs.write(
            "index_factor_sync_failed",
            level="ERROR",
            **common,
            completed_at=completed_at,
            error_category=getattr(exc, "category", "UNEXPECTED"),
            error_summary=getattr(exc, "summary", "指数因子同步失败"),
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=_WINDOW_TARGET_MS,
                )
            ),
        )
        raise
    completed_at = datetime.now(UTC)
    payload = _serialize_result(result)
    payload = {key: value for key, value in payload.items() if key not in common}
    if result.status is IndexFactorSyncStatus.SKIPPED:
        logs.write(
            "index_factor_sync_skipped",
            **common,
            **payload,
            skipped=True,
            completed_at=completed_at,
        )
    else:
        logs.write(
            "index_factor_sync_succeeded",
            **common,
            **payload,
            completed_at=completed_at,
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=_WINDOW_TARGET_MS,
                )
            ),
        )
    return payload


@flow(name="index-factor-backfill", retries=0)
def index_factor_backfill(
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    """历史回补：区间整体校验后按交易日历逐日展开，逐日独立终态（spec FR-003）。"""
    settings = Settings()
    batch_id = backfill_batch_id.strip()
    if not batch_id:
        raise ValueError("backfill_batch_id 不能为空")
    _validate_backfill_range(start_date, end_date, settings.index_factor_timezone)
    service = _build_service(settings)
    trade_days = _load_trade_days(settings, start_date, end_date)
    days = expand_backfill_dates(start_date, end_date, trade_days=trade_days)
    succeeded = failed = skipped = in_progress = 0
    failed_dates: list[str] = []
    for target in days:
        resolution = service.resolve_backfill_date(
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
            RetryIndexFactorSyncCommand(
                resolution.run_id or "", f"{flow_run_id}:{target}:retry"
            )
            if resolution.action is BackfillDateAction.RETRY
            else BackfillIndexFactorCommand(
                target, batch_id, f"{flow_run_id}:{target}:backfill"
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
    if start_date < BACKFILL_START:
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


def _serialize_result(result: IndexFactorSyncResult) -> dict[str, Any]:
    payload: dict[str, Any] = asdict(result)
    for key, value in tuple(payload.items()):
        if isinstance(value, date):
            payload[key] = value.isoformat()
        elif hasattr(value, "value"):
            payload[key] = value.value
    return payload


def _log_store(settings: Settings) -> JsonlLogStore:
    return JsonlLogStore(
        settings.index_factor_log_dir,
        filename=settings.index_factor_log_filename,
    )
