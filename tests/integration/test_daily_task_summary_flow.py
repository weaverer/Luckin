from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

from sqlalchemy import delete, select

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.flows.daily_task_summary import scheduled_business_date
from lucking.models.workbench import (
    DailyTaskNotificationAttempt,
    DailyTaskSummary,
    DailyTaskSummaryItem,
)
from lucking.ports.notification_sender import NotificationDisposition
from lucking.ports.task_execution_reader import TaskExecution, TaskExecutionStatus
from lucking.repositories.workbench.task_summaries import SqlAlchemyTaskSummaryRepository
from lucking.services.daily_task_summary import DailyTaskSummaryService
from tests.contract.memory_notification_sender import MemoryNotificationSender


def test_summary_uses_shanghai_business_date_at_original_20_clock() -> None:
    scheduled_for = datetime(2026, 8, 8, 12, tzinfo=UTC)
    assert scheduled_business_date(scheduled_for) == date(2026, 8, 8)


class Reader:
    def __init__(self, status: TaskExecutionStatus) -> None:
        self.status = status

    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]:
        return [
            TaskExecution(
                task_key="daily-quote",
                schedule_slug="daily-quote-sync",
                display_name="日线行情同步",
                source_domain="market-data",
                business_date=business_date,
                status=self.status,
                observed_at=observed_at,
            )
        ]


def test_snapshot_is_immutable_and_automatic_notification_is_idempotent() -> None:
    sessions = create_session_factory(create_database_engine(Settings()))
    repository = SqlAlchemyTaskSummaryRepository(sessions)
    sender = MemoryNotificationSender()
    business_date = date(2099, 8, 10)
    failed_business_date = date(2099, 8, 11)
    scheduled_for = datetime(2099, 8, 10, 12, tzinfo=UTC)
    completed_at = datetime(2099, 8, 10, 12, 4, tzinfo=UTC)
    try:
        first_service = DailyTaskSummaryService(
            repository,
            [Reader(TaskExecutionStatus.FAILED)],
            sender,
            now=lambda: completed_at,
        )
        first = first_service.build(business_date, scheduled_for)
        changed_service = DailyTaskSummaryService(
            repository,
            [Reader(TaskExecutionStatus.SUCCEEDED)],
            sender,
            now=lambda: completed_at,
        )
        repeated = changed_service.build(business_date, scheduled_for)
        assert repeated.summary_id == first.summary_id
        assert repeated.snapshot.digest == first.snapshot.digest
        assert repeated.snapshot.counts[TaskExecutionStatus.FAILED] == 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: first_service.notify(first.summary_id), range(2)))
        assert sum(result is not None for result in results) == 1
        assert len(sender.messages) == 1
        notified = repository.get(first.summary_id)
        assert notified.notified_at is not None
        assert notified.notified_at <= scheduled_for.replace(minute=5)
        assert first_service.notify(first.summary_id, manual_retry=True) is not None
        assert len(sender.messages) == 2
        assert sender.messages[0].idempotency_key == sender.messages[1].idempotency_key

        failed_sender = MemoryNotificationSender(NotificationDisposition.PERMANENT_FAILURE)
        failed_service = DailyTaskSummaryService(
            repository,
            [Reader(TaskExecutionStatus.SUCCEEDED)],
            failed_sender,
            now=lambda: completed_at,
        )
        failed = failed_service.build(failed_business_date, scheduled_for)
        assert failed_service.notify(failed.summary_id) is not None
        failed_history = repository.get_by_business_date(failed_business_date)
        assert failed_history is not None
        assert failed_history.notification_status == "FAILED"
        with sessions() as session:
            failed_attempt = session.scalar(
                select(DailyTaskNotificationAttempt).where(
                    DailyTaskNotificationAttempt.summary_id
                    == session.scalar(
                        select(DailyTaskSummary.id).where(
                            DailyTaskSummary.summary_id == failed.summary_id
                        )
                    )
                )
            )
            assert failed_attempt is not None
            assert failed_attempt.completed_at == completed_at.replace(tzinfo=None)
    finally:
        with sessions.begin() as session:
            summary_ids = list(
                session.scalars(
                    select(DailyTaskSummary.id).where(
                        DailyTaskSummary.business_date.in_((business_date, failed_business_date))
                    )
                )
            )
            session.execute(
                delete(DailyTaskNotificationAttempt).where(
                    DailyTaskNotificationAttempt.summary_id.in_(summary_ids)
                )
            )
            session.execute(
                delete(DailyTaskSummaryItem).where(DailyTaskSummaryItem.summary_id.in_(summary_ids))
            )
            session.execute(delete(DailyTaskSummary).where(DailyTaskSummary.id.in_(summary_ids)))
