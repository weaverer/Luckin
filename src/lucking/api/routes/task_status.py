"""Strongly typed live task status and immutable summary APIs."""

from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from lucking.api.dependencies import get_current_session, get_request_id, get_settings
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, success_response
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.task_readers.broker_recommendation import (
    BrokerRecommendationTaskReader,
)
from lucking.integrations.task_readers.index_factor import index_factor_reader
from lucking.integrations.task_readers.market_data import market_data_reader
from lucking.integrations.task_readers.shareholder_data import shareholder_data_reader
from lucking.integrations.task_readers.stock_factor import stock_factor_reader
from lucking.integrations.task_readers.stock_list import StockListTaskReader
from lucking.integrations.task_readers.trading_calendar import TradingCalendarTaskReader
from lucking.models.workbench import (
    NotificationAttemptStatus,
    NotificationStatus,
    NotificationTriggerKind,
    SummaryStatus,
)
from lucking.ports.task_execution_reader import (
    TaskExecution,
    TaskExecutionReader,
    TaskExecutionStatus,
)
from lucking.repositories.workbench.task_summaries import SqlAlchemyTaskSummaryRepository
from lucking.services.auth import AuthenticatedSession
from lucking.services.daily_task_summary import (
    DailyTaskStatusQuery,
    StoredSummary,
    SummarySnapshot,
)

router = APIRouter(tags=["task-status"])


class TaskStatusItemDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_key: str
    schedule_slug: str
    display_name: str
    status: TaskExecutionStatus
    source_run_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    record_count: int | None
    error_category: str | None
    error_summary: str | None
    observed_at: datetime


class TaskStatusDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date
    observed_at: datetime
    items: list[TaskStatusItemDto]


class TaskStatusCountsDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    SUCCEEDED: int = Field(ge=0)
    PARTIAL: int = Field(ge=0)
    FAILED: int = Field(ge=0)
    RUNNING: int = Field(ge=0)
    UNKNOWN: int = Field(ge=0)
    NOT_RUN: int = Field(ge=0)


class NotificationAttemptDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_no: int = Field(ge=1)
    trigger_kind: NotificationTriggerKind
    status: NotificationAttemptStatus
    error_category: str | None
    error_summary: str | None
    started_at: datetime
    completed_at: datetime | None
    retryable: bool


class TaskSummaryDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_id: str
    business_date: date
    status: SummaryStatus
    notification_status: NotificationStatus
    generated_at: datetime | None
    notified_at: datetime | None
    counts: TaskStatusCountsDto
    items: list[TaskStatusItemDto]
    latest_notification_attempt: NotificationAttemptDto | None


@lru_cache
def query_service() -> DailyTaskStatusQuery:
    sessions = create_session_factory(create_database_engine(get_settings()))
    readers: list[TaskExecutionReader] = [
        TradingCalendarTaskReader(sessions),
        StockListTaskReader(sessions),
        market_data_reader(sessions),
        index_factor_reader(sessions),
        stock_factor_reader(sessions),
        shareholder_data_reader(sessions),
        BrokerRecommendationTaskReader(sessions),
    ]
    return DailyTaskStatusQuery(SqlAlchemyTaskSummaryRepository(sessions), readers)


def item_dto(item: TaskExecution) -> TaskStatusItemDto:
    return TaskStatusItemDto(
        task_key=item.task_key,
        schedule_slug=item.schedule_slug,
        display_name=item.display_name,
        status=item.status,
        source_run_id=item.source_run_id,
        started_at=item.started_at,
        completed_at=item.completed_at,
        record_count=item.record_count,
        error_category=item.error_category,
        error_summary=item.error_summary,
        observed_at=item.observed_at,
    )


def live_dto(snapshot: SummarySnapshot) -> TaskStatusDto:
    return TaskStatusDto(
        business_date=snapshot.business_date,
        observed_at=snapshot.scheduled_for,
        items=[item_dto(item) for item in snapshot.executions],
    )


def summary_dto(summary: StoredSummary) -> TaskSummaryDto:
    attempt = summary.latest_notification_attempt
    return TaskSummaryDto(
        summary_id=summary.summary_id,
        business_date=summary.snapshot.business_date,
        status=SummaryStatus(summary.status),
        notification_status=NotificationStatus(summary.notification_status),
        generated_at=summary.generated_at,
        notified_at=summary.notified_at,
        counts=TaskStatusCountsDto(
            SUCCEEDED=summary.snapshot.counts[TaskExecutionStatus.SUCCEEDED],
            PARTIAL=summary.snapshot.counts[TaskExecutionStatus.PARTIAL],
            FAILED=summary.snapshot.counts[TaskExecutionStatus.FAILED],
            RUNNING=summary.snapshot.counts[TaskExecutionStatus.RUNNING],
            UNKNOWN=summary.snapshot.counts[TaskExecutionStatus.UNKNOWN],
            NOT_RUN=summary.snapshot.counts[TaskExecutionStatus.NOT_RUN],
        ),
        items=[item_dto(item) for item in summary.snapshot.executions],
        latest_notification_attempt=(
            NotificationAttemptDto(
                attempt_no=attempt.attempt_no,
                trigger_kind=NotificationTriggerKind(attempt.trigger_kind),
                status=NotificationAttemptStatus(attempt.status),
                error_category=attempt.error_category,
                error_summary=attempt.error_summary,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                retryable=(
                    attempt.status == "FAILED"
                    and attempt.error_category in {"NETWORK", "RATE_LIMITED", "UPSTREAM"}
                ),
            )
            if attempt
            else None
        ),
    )


@router.get(
    "/task-status",
    response_model=ApiResponse[TaskStatusDto],
    operation_id="listTaskStatus",
)
async def list_status(
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
    business_date: date | None = None,
) -> ApiResponse[TaskStatusDto]:
    observed_at = datetime.now(UTC)
    target = business_date or observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return success_response(live_dto(query_service().live(target, observed_at)), request_id)


@router.get(
    "/task-summaries/{business_date}",
    response_model=ApiResponse[TaskSummaryDto],
    operation_id="getTaskSummary",
)
async def get_summary(
    business_date: date,
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> ApiResponse[TaskSummaryDto]:
    summary = query_service().history(business_date)
    if summary is None:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, "汇总不存在")
    return success_response(summary_dto(summary), request_id)
