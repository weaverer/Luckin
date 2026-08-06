"""Prefect 工作流：股东数据三接口计划同步与人工回补（3 增量 + 3 回补）。

三个接口拆分为 3 套独立 Flow（用户显式要求）：任一接口失败只影响自身
run 终态，不影响其他两个接口，可单独重跑（prefect-flow.md §1/§3）。
流程名称使用简体中文且语义符合业务场景（spec FR-019）；内部
``schedule_slug`` 保持 ASCII（幂等键与审计标识，research 决策 6）。
回补提取范围按接口语义：``top10_*`` 按报告期季度末、``stk_holdernumber``
按公告日逐日（research 决策 1）；运维约定三回补串行执行。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.runtime import flow_run

from lucking.clickhouse import build_clickhouse_client
from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.registry import build_shareholder_data_provider
from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.models.market_data import DataKind
from lucking.repositories.market_data import (
    BackfillDateAction,
    SqlAlchemyMarketDataRepository,
)
from lucking.repositories.shareholder_data_clickhouse import (
    ShareholderDataClickHouseRepository,
)
from lucking.services.shareholder_data import (
    BackfillShareholderDataCommand,
    RetryShareholderDataSyncCommand,
    ScheduledShareholderDataSyncCommand,
    ShareholderDataService,
    ShareholderDataSyncResult,
    ShareholderDataSyncStatus,
)

BACKFILL_START = date(2024, 1, 1)
_WINDOW_TARGET_MS = 8 * 60 * 60 * 1000  # 17:00 错峰启动当日形成终态

# 接口 → (增量 Flow 名, 回补 Flow 名, schedule_slug)
_FLOWS: dict[str, tuple[str, str, str]] = {
    "TOP10": ("前十大股东交易日同步", "前十大股东历史回补", "top10-holders-sync"),
    "TOP10_FLOAT": (
        "前十大流通股东交易日同步",
        "前十大流通股东历史回补",
        "top10-floatholders-sync",
    ),
    "HOLDER_COUNT": ("股东人数交易日同步", "股东人数历史回补", "holder-count-sync"),
}

_QUARTER_END_DAY = {3: 31, 6: 30, 9: 30, 12: 31}


def _build_service(settings: Settings) -> ShareholderDataService:
    provider = build_shareholder_data_provider(settings.shareholder_data_provider, settings)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyMarketDataRepository(
        session_factory,
        lease_seconds=settings.shareholder_data_run_lease_seconds,
    )
    clickhouse = ShareholderDataClickHouseRepository(build_clickhouse_client(settings))
    return ShareholderDataService(
        provider,
        repository,
        clickhouse,
        session_factory,
        timezone=settings.shareholder_data_timezone,
        fetch_deadline_seconds=settings.shareholder_data_fetch_deadline_seconds,
        page_limit=settings.shareholder_data_page_limit,
        window_lookback_days=settings.shareholder_data_window_lookback_days,
    )


def _log_store(settings: Settings) -> JsonlLogStore:
    return JsonlLogStore(
        settings.shareholder_data_log_dir,
        filename=settings.shareholder_data_log_filename,
    )


@flow(name="前十大股东交易日同步", retries=0)
def top10_holders_sync(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    """每个交易日 17:00 同步前十大股东新增披露（spec FR-002）。"""
    return _run_incremental("TOP10", scheduled_at, schedule_slug)


@flow(name="前十大流通股东交易日同步", retries=0)
def top10_float_holders_sync(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    """每个交易日 17:05 同步前十大流通股东新增披露（spec FR-002）。"""
    return _run_incremental("TOP10_FLOAT", scheduled_at, schedule_slug)


@flow(name="股东人数交易日同步", retries=0)
def holder_count_sync(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]:
    """每个交易日 17:10 同步股东人数新增披露（spec FR-002）。"""
    return _run_incremental("HOLDER_COUNT", scheduled_at, schedule_slug)


@flow(name="前十大股东历史回补", retries=0)
def top10_holders_backfill(
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    """前十大股东历史回补：按报告期季度末提取（spec FR-003）。"""
    return _run_backfill("TOP10", start_date, end_date, backfill_batch_id)


@flow(name="前十大流通股东历史回补", retries=0)
def top10_float_holders_backfill(
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    """前十大流通股东历史回补：按报告期季度末提取（spec FR-003）。"""
    return _run_backfill("TOP10_FLOAT", start_date, end_date, backfill_batch_id)


@flow(name="股东人数历史回补", retries=0)
def holder_count_backfill(
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    """股东人数历史回补：按公告日逐日提取（spec FR-003）。"""
    return _run_backfill("HOLDER_COUNT", start_date, end_date, backfill_batch_id)


def _run_incremental(
    kind: str,
    scheduled_at: datetime | None,
    schedule_slug: str | None,
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
    event = _FLOWS[kind][0]
    common: dict[str, Any] = {
        "flow_run_id": flow_run_id,
        "schedule_slug": actual_slug,
        "scheduled_at": actual_scheduled_at,
        "run_kind": "SCHEDULED",
        "started_at": started_at,
    }
    logs.write(f"{event}_started", **common)
    try:
        result = _run_sync_command(
            settings,
            kind,
            ScheduledShareholderDataSyncCommand(actual_slug, actual_scheduled_at, flow_run_id),
        )
    except Exception as exc:
        completed_at = datetime.now(UTC)
        logs.write(
            f"{event}_failed",
            level="ERROR",
            **common,
            completed_at=completed_at,
            error_category=getattr(exc, "category", "UNEXPECTED"),
            error_summary=getattr(exc, "summary", "股东数据同步失败"),
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
    if result.status is ShareholderDataSyncStatus.SKIPPED:
        logs.write(
            f"{event}_skipped",
            **common,
            **payload,
            skipped=True,
            completed_at=completed_at,
        )
    else:
        logs.write(
            f"{event}_succeeded",
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


def _run_backfill(
    kind: str,
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    settings = Settings()
    batch_id = backfill_batch_id.strip()
    if not batch_id:
        raise ValueError("backfill_batch_id 不能为空")
    _validate_backfill_range(start_date, end_date, settings.shareholder_data_timezone)
    service = _build_service(settings)
    data_kind = DataKind(
        "TOP10_HOLDERS"
        if kind == "TOP10"
        else "TOP10_FLOAT_HOLDERS"
        if kind == "TOP10_FLOAT"
        else "HOLDER_COUNT"
    )
    days = _expansion(kind, start_date, end_date)
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
            RetryShareholderDataSyncCommand(
                resolution.run_id or "", f"{flow_run_id}:{target}:retry"
            )
            if resolution.action is BackfillDateAction.RETRY
            else BackfillShareholderDataCommand(
                target, batch_id, f"{flow_run_id}:{target}:backfill"
            )
        )
        try:
            _run_sync_command(settings, kind, command)
            succeeded += 1
        except Exception:
            failed += 1
            failed_dates.append(target.isoformat())
    return {
        "backfill_batch_id": batch_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_day_count": len(days),
        "succeeded_day_count": succeeded,
        "failed_day_count": failed,
        "skipped_day_count": skipped,
        "in_progress_day_count": in_progress,
        "failed_dates": failed_dates,
    }


def _run_sync_command(
    settings: Settings, kind: str, command: Any
) -> ShareholderDataSyncResult:
    """按命令类型分发到对应接口的 Service 入口（增量/回补/重试）。"""
    service = _build_service(settings)
    if isinstance(command, RetryShareholderDataSyncCommand):
        return service.retry(kind, command)
    if isinstance(command, BackfillShareholderDataCommand):
        if kind == "TOP10":
            return service.backfill_top10_holders(command)
        if kind == "TOP10_FLOAT":
            return service.backfill_top10_float_holders(command)
        return service.backfill_holder_count(command)
    if kind == "TOP10":
        return service.sync_top10_holders(command)
    if kind == "TOP10_FLOAT":
        return service.sync_top10_float_holders(command)
    return service.sync_holder_count(command)


def _expansion(kind: str, start_date: date, end_date: date) -> tuple[date, ...]:
    """回补日期展开：top10 按报告期季度末、股东人数按公告日逐日。"""
    if kind == "HOLDER_COUNT":
        return tuple(
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        )
    days: list[date] = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        if month in _QUARTER_END_DAY:  # 仅季度月（3/6/9/12）产生报告期
            quarter_end = date(year, month, _QUARTER_END_DAY[month])
            if start_date <= quarter_end <= end_date:
                days.append(quarter_end)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return tuple(days)


def _validate_backfill_range(start_date: date, end_date: date, timezone: str) -> None:
    if start_date > end_date:
        raise ValueError("开始日期不得晚于结束日期")
    if start_date < BACKFILL_START:
        raise ValueError("回补不得早于 2024-01-01")
    if end_date > datetime.now(ZoneInfo(timezone)).date():
        raise ValueError("回补不得包含未来日期")


def _serialize_result(result: ShareholderDataSyncResult) -> dict[str, Any]:
    payload: dict[str, Any] = asdict(result)
    for key, value in tuple(payload.items()):
        if isinstance(value, date):
            payload[key] = value.isoformat()
        elif hasattr(value, "value"):
            payload[key] = value.value
    return payload
