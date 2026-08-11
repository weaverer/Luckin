"""Short-transaction persistence for immutable task snapshots and notification attempts."""

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.db import as_utc_naive
from lucking.models.workbench import (
    DailyTaskNotificationAttempt,
    DailyTaskSummary,
    DailyTaskSummaryItem,
)
from lucking.ports.notification_sender import NotificationDisposition, NotificationResult
from lucking.ports.task_execution_reader import TaskExecution, TaskExecutionStatus
from lucking.services.daily_task_summary import (
    NotificationAttemptView,
    NotificationClaim,
    StoredSummary,
    SummarySnapshot,
)


class TaskSummaryNotFound(LookupError):
    pass


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyTaskSummaryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_or_get(self, snapshot: SummarySnapshot) -> StoredSummary:
        try:
            with self._sessions.begin() as session:
                existing = session.scalar(
                    select(DailyTaskSummary)
                    .where(DailyTaskSummary.business_date == snapshot.business_date)
                    .with_for_update()
                )
                if existing is not None:
                    return self._load(session, existing)
                return self._create(session, snapshot)
        except IntegrityError:
            with self._sessions() as session:
                existing = session.scalar(
                    select(DailyTaskSummary).where(
                        DailyTaskSummary.business_date == snapshot.business_date
                    )
                )
                if existing is None:
                    raise
                return self._load(session, existing)

    def _create(self, session: Session, snapshot: SummarySnapshot) -> StoredSummary:
        counts = snapshot.counts
        row = DailyTaskSummary(
            summary_id=str(uuid4()),
            business_date=snapshot.business_date,
            scheduled_for=as_utc_naive(snapshot.scheduled_for),
            status="READY",
            notification_status="PENDING",
            total_count=snapshot.total_count,
            succeeded_count=counts[TaskExecutionStatus.SUCCEEDED],
            partial_count=counts[TaskExecutionStatus.PARTIAL],
            failed_count=counts[TaskExecutionStatus.FAILED],
            running_count=counts[TaskExecutionStatus.RUNNING],
            unknown_count=counts[TaskExecutionStatus.UNKNOWN],
            not_run_count=counts[TaskExecutionStatus.NOT_RUN],
            snapshot_digest=snapshot.digest,
            generated_at=as_utc_naive(datetime.now(UTC)),
            notified_at=None,
        )
        session.add(row)
        session.flush()
        for execution in snapshot.executions:
            session.add(
                DailyTaskSummaryItem(
                    item_id=str(uuid4()),
                    summary_id=row.id,
                    task_key=execution.task_key,
                    schedule_slug=execution.schedule_slug,
                    display_name=execution.display_name,
                    source_domain=execution.source_domain,
                    status=execution.status.value,
                    source_run_id=execution.source_run_id,
                    source_flow_run_id=execution.source_flow_run_id,
                    started_at=(
                        as_utc_naive(execution.started_at) if execution.started_at else None
                    ),
                    completed_at=(
                        as_utc_naive(execution.completed_at) if execution.completed_at else None
                    ),
                    record_count=execution.record_count,
                    error_category=execution.error_category,
                    error_summary=execution.error_summary,
                    observed_at=as_utc_naive(execution.observed_at),
                )
            )
        session.flush()
        return self._load(session, row)

    def get(self, summary_id: str) -> StoredSummary:
        with self._sessions() as session:
            row = session.scalar(
                select(DailyTaskSummary).where(DailyTaskSummary.summary_id == summary_id)
            )
            if row is None:
                raise TaskSummaryNotFound("任务汇总不存在")
            return self._load(session, row)

    def get_by_business_date(self, business_date: date) -> StoredSummary | None:
        with self._sessions() as session:
            row = session.scalar(
                select(DailyTaskSummary).where(DailyTaskSummary.business_date == business_date)
            )
            return self._load(session, row) if row is not None else None

    def claim_notification(
        self, summary_id: str, *, manual_retry: bool
    ) -> NotificationClaim | None:
        with self._sessions.begin() as session:
            summary = session.scalar(
                select(DailyTaskSummary)
                .where(DailyTaskSummary.summary_id == summary_id)
                .with_for_update()
            )
            if summary is None:
                raise TaskSummaryNotFound("任务汇总不存在")
            if not manual_retry and summary.notification_status == "SENT":
                return None
            running = session.scalar(
                select(DailyTaskNotificationAttempt.id).where(
                    DailyTaskNotificationAttempt.summary_id == summary.id,
                    DailyTaskNotificationAttempt.status == "RUNNING",
                )
            )
            if running is not None:
                return None
            attempt_no = (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(DailyTaskNotificationAttempt)
                        .where(DailyTaskNotificationAttempt.summary_id == summary.id)
                    )
                    or 0
                )
                + 1
            )
            attempt = DailyTaskNotificationAttempt(
                attempt_id=str(uuid4()),
                summary_id=summary.id,
                attempt_no=attempt_no,
                trigger_kind="MANUAL_RETRY" if manual_retry else "AUTOMATIC",
                status="RUNNING",
                provider_code="pending",
                response_status=None,
                error_category=None,
                error_summary=None,
                started_at=as_utc_naive(datetime.now(UTC)),
                completed_at=None,
            )
            summary.notification_status = "SENDING"
            session.add(attempt)
            session.flush()
            return NotificationClaim(attempt.attempt_id, self._load(session, summary))

    def complete_notification(
        self, attempt_id: str, result: NotificationResult, completed_at: datetime
    ) -> None:
        with self._sessions.begin() as session:
            attempt = session.scalar(
                select(DailyTaskNotificationAttempt)
                .where(DailyTaskNotificationAttempt.attempt_id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise TaskSummaryNotFound("通知尝试不存在")
            summary = session.get(DailyTaskSummary, attempt.summary_id)
            if summary is None:
                raise TaskSummaryNotFound("任务汇总不存在")
            delivered = result.disposition is NotificationDisposition.DELIVERED
            attempt.status = "SUCCEEDED" if delivered else "FAILED"
            attempt.provider_code = result.provider_code
            attempt.response_status = result.response_status
            attempt.error_category = result.error_category
            attempt.error_summary = result.error_summary
            attempt.completed_at = as_utc_naive(completed_at)
            summary.notification_status = "SENT" if delivered else "FAILED"
            if delivered:
                summary.notified_at = as_utc_naive(completed_at)

    def _load(self, session: Session, row: DailyTaskSummary) -> StoredSummary:
        items = list(
            session.scalars(
                select(DailyTaskSummaryItem)
                .where(DailyTaskSummaryItem.summary_id == row.id)
                .order_by(DailyTaskSummaryItem.task_key)
            )
        )
        executions = [
            TaskExecution(
                task_key=item.task_key,
                schedule_slug=item.schedule_slug,
                display_name=item.display_name,
                source_domain=item.source_domain,
                business_date=row.business_date,
                status=TaskExecutionStatus(item.status),
                observed_at=_aware(item.observed_at),
                source_run_id=item.source_run_id,
                source_flow_run_id=item.source_flow_run_id,
                started_at=_aware(item.started_at) if item.started_at else None,
                completed_at=_aware(item.completed_at) if item.completed_at else None,
                record_count=item.record_count,
                error_category=item.error_category,
                error_summary=item.error_summary,
            )
            for item in items
        ]
        latest_attempt = session.scalar(
            select(DailyTaskNotificationAttempt)
            .where(DailyTaskNotificationAttempt.summary_id == row.id)
            .order_by(DailyTaskNotificationAttempt.attempt_no.desc())
            .limit(1)
        )
        counts = {
            TaskExecutionStatus.SUCCEEDED: row.succeeded_count,
            TaskExecutionStatus.PARTIAL: row.partial_count,
            TaskExecutionStatus.FAILED: row.failed_count,
            TaskExecutionStatus.RUNNING: row.running_count,
            TaskExecutionStatus.UNKNOWN: row.unknown_count,
            TaskExecutionStatus.NOT_RUN: row.not_run_count,
        }
        snapshot = SummarySnapshot(
            row.business_date,
            _aware(row.scheduled_for),
            tuple(executions),
            counts,
            row.snapshot_digest or "",
        )
        return StoredSummary(
            row.summary_id,
            row.notification_status,
            snapshot,
            row.status,
            _aware(row.generated_at) if row.generated_at else None,
            _aware(row.notified_at) if row.notified_at else None,
            (
                NotificationAttemptView(
                    attempt_no=latest_attempt.attempt_no,
                    trigger_kind=latest_attempt.trigger_kind,
                    status=latest_attempt.status,
                    error_category=latest_attempt.error_category,
                    error_summary=latest_attempt.error_summary,
                    started_at=_aware(latest_attempt.started_at),
                    completed_at=(
                        _aware(latest_attempt.completed_at)
                        if latest_attempt.completed_at
                        else None
                    ),
                )
                if latest_attempt
                else None
            ),
        )
