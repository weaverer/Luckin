"""Prefect composition root for daily stock-list synchronization."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from prefect import flow

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.registry import build_stock_list_provider
from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.ports.stock_list_provider import ScopeCode
from lucking.repositories.stock_list import SqlAlchemyStockListRepository
from lucking.services.stock_list import StockListService, StockListSyncCommand


def _build_service(settings: Settings) -> StockListService:
    provider = build_stock_list_provider(settings.stock_list_provider, settings)
    engine = create_database_engine(settings)
    repository = SqlAlchemyStockListRepository(create_session_factory(engine))
    return StockListService(
        provider,
        repository,
        fetch_deadline_seconds=settings.stock_list_fetch_deadline_seconds,
    )


@flow(name="stock-list-sync", retries=0)
def sync_stock_list(
    scope_code: str = "CN-S",
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
    is_manual_retry: bool = False,
) -> dict[str, Any]:
    settings = Settings()
    if scope_code != "CN-S":
        raise ValueError("首期 scope_code 只允许 CN-S")
    actual_schedule_slug = schedule_slug or "manual-stock-list"
    actual_scheduled_at = scheduled_at or datetime.now(UTC)
    if actual_scheduled_at.tzinfo is None:
        raise ValueError("scheduled_at 必须包含时区")
    flow_run_id = str(uuid4())
    started_at = datetime.now(UTC)
    log_store = JsonlLogStore(
        settings.stock_list_log_dir,
        filename=settings.stock_list_log_filename,
    )
    common: dict[str, Any] = {
        "flow_run_id": flow_run_id,
        "schedule_slug": actual_schedule_slug,
        "scope_code": scope_code,
        "scheduled_at": actual_scheduled_at,
        "started_at": started_at,
    }
    log_store.write("stock_list_sync_started", **common)
    try:
        result = _build_service(settings).sync(
            StockListSyncCommand(
                actual_schedule_slug,
                actual_scheduled_at,
                ScopeCode.CN_STOCK,
                flow_run_id,
                is_manual_retry,
            )
        )
    except Exception as exc:
        completed_at = datetime.now(UTC)
        log_store.write(
            "stock_list_sync_failed",
            level="ERROR",
            **common,
            completed_at=completed_at,
            error_category=getattr(exc, "category", "UNEXPECTED"),
            error_summary=str(exc),
            **asdict(
                calculate_schedule_timing(
                    actual_scheduled_at,
                    started_at,
                    completed_at,
                    target_ms=settings.stock_list_timeliness_target_ms,
                )
            ),
        )
        raise
    completed_at = datetime.now(UTC)
    data = asdict(result)
    data["status"] = result.status.value
    data["business_date"] = result.business_date.isoformat()
    log_store.write(
        "stock_list_validation_completed",
        **common,
        **data,
        completed_at=completed_at,
    )
    log_store.write(
        "stock_list_sync_succeeded",
        **common,
        **data,
        completed_at=completed_at,
        **asdict(
            calculate_schedule_timing(
                actual_scheduled_at,
                started_at,
                completed_at,
                target_ms=settings.stock_list_timeliness_target_ms,
            )
        ),
    )
    return data
