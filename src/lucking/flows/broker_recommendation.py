"""Prefect workflows for monthly broker recommendations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.runtime import flow_run

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.registry import build_broker_recommendation_provider
from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.repositories.broker_recommendation import (
    BackfillMonthAction,
    SqlAlchemyBrokerRecommendationRepository,
)
from lucking.services.broker_recommendation import (
    BackfillBrokerRecommendationMonthCommand,
    BrokerRecommendationService,
    BrokerRecommendationSyncResult,
    RetryBrokerRecommendationSyncCommand,
    ScheduledBrokerRecommendationSyncCommand,
)


def _build_service(settings: Settings) -> BrokerRecommendationService:
    provider = build_broker_recommendation_provider(
        settings.broker_recommendation_provider, settings
    )
    engine = create_database_engine(settings)
    repository = SqlAlchemyBrokerRecommendationRepository(
        create_session_factory(engine),
        lease_seconds=settings.broker_recommendation_run_lease_seconds,
    )
    return BrokerRecommendationService(
        provider,
        repository,
        timezone=settings.broker_recommendation_timezone,
        fetch_deadline_seconds=settings.broker_recommendation_fetch_deadline_seconds,
        page_limit=settings.broker_recommendation_page_limit,
    )


@flow(name="broker-recommendation-sync", retries=0)
def sync_broker_recommendations(
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
        "schedule_slug": actual_slug,
        "scheduled_at": actual_scheduled_at,
        "run_kind": "SCHEDULED",
        "started_at": started_at,
    }
    logs.write("broker_recommendation_sync_started", **common)
    try:
        result = _build_service(settings).sync(
            ScheduledBrokerRecommendationSyncCommand(actual_slug, actual_scheduled_at, flow_run_id)
        )
    except Exception as exc:
        completed_at = datetime.now(UTC)
        logs.write(
            "broker_recommendation_sync_failed",
            level="ERROR",
            **common,
            completed_at=completed_at,
            error_category=getattr(exc, "category", "UNEXPECTED"),
            error_summary=getattr(exc, "summary", "券商金股同步失败"),
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=settings.broker_recommendation_timeliness_target_ms,
                )
            ),
        )
        raise
    completed_at = datetime.now(UTC)
    payload = _serialize_result(result)
    logs.write(
        "broker_recommendation_validation_completed",
        **common,
        **payload,
        completed_at=completed_at,
    )
    logs.write(
        "broker_recommendation_sync_succeeded",
        **common,
        **payload,
        completed_at=completed_at,
        **asdict(
            calculate_schedule_timing(
                actual_scheduled_at,
                started_at,
                completed_at,
                target_ms=settings.broker_recommendation_timeliness_target_ms,
            )
        ),
    )
    return payload


@flow(name="broker-recommendation-backfill", retries=0)
def backfill_broker_recommendations(
    start_month: date,
    end_month: date,
    backfill_batch_id: str,
) -> dict[str, Any]:
    settings = Settings()
    months = expand_month_range(
        start_month,
        end_month,
        timezone=settings.broker_recommendation_timezone,
        max_months=settings.broker_recommendation_backfill_max_months,
    )
    batch_id = backfill_batch_id.strip()
    if not batch_id:
        raise ValueError("backfill_batch_id 不能为空")
    service = _build_service(settings)
    succeeded = failed = skipped = in_progress = 0
    failed_months: list[str] = []
    for target in months:
        resolution = service.resolve_backfill_month(backfill_batch_id=batch_id, target_month=target)
        if resolution.action is BackfillMonthAction.SKIP_SUCCEEDED:
            skipped += 1
            continue
        if resolution.action is BackfillMonthAction.IN_PROGRESS:
            in_progress += 1
            continue
        command = (
            RetryBrokerRecommendationSyncCommand(
                resolution.run_id or "", f"{flow_run.id or uuid4()}:{target}:retry"
            )
            if resolution.action is BackfillMonthAction.RETRY
            else BackfillBrokerRecommendationMonthCommand(
                target, batch_id, f"{flow_run.id or uuid4()}:{target}:backfill"
            )
        )
        try:
            service.sync(command)
            succeeded += 1
        except Exception:
            failed += 1
            failed_months.append(target.isoformat())
    return {
        "backfill_batch_id": batch_id,
        "start_month": start_month.isoformat(),
        "end_month": end_month.isoformat(),
        "total_month_count": len(months),
        "succeeded_month_count": succeeded,
        "failed_month_count": failed,
        "skipped_month_count": skipped,
        "in_progress_month_count": in_progress,
        "failed_months": failed_months,
    }


@flow(name="broker-recommendation-retry", retries=0)
def retry_broker_recommendation_sync(run_id: str) -> dict[str, Any]:
    settings = Settings()
    result = _build_service(settings).sync(
        RetryBrokerRecommendationSyncCommand(run_id.strip(), flow_run.id or str(uuid4()))
    )
    return _serialize_result(result)


def expand_month_range(
    start_month: date,
    end_month: date,
    *,
    timezone: str = "Asia/Shanghai",
    max_months: int = 120,
    today: date | None = None,
) -> tuple[date, ...]:
    if start_month.day != 1 or end_month.day != 1:
        raise ValueError("月份范围端点必须是月首")
    if start_month > end_month:
        raise ValueError("开始月份不得晚于结束月份")
    current_month = (today or datetime.now(ZoneInfo(timezone)).date()).replace(day=1)
    if end_month > current_month:
        raise ValueError("补跑范围不得包含未来月份")
    count = (end_month.year - start_month.year) * 12 + end_month.month - start_month.month + 1
    if not 1 <= count <= max_months:
        raise ValueError(f"补跑范围必须为 1–{max_months} 个月")
    result: list[date] = []
    year, month = start_month.year, start_month.month
    for _ in range(count):
        result.append(date(year, month, 1))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(result)


def _serialize_result(result: BrokerRecommendationSyncResult) -> dict[str, Any]:
    payload: dict[str, Any] = asdict(result)
    for key, value in tuple(payload.items()):
        if isinstance(value, date):
            payload[key] = value.isoformat()
        elif hasattr(value, "value"):
            payload[key] = value.value
    return payload


def _log_store(settings: Settings) -> JsonlLogStore:
    return JsonlLogStore(
        settings.broker_recommendation_log_dir,
        filename=settings.broker_recommendation_log_filename,
    )
