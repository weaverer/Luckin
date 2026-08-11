"""Shared reader for scheduled runs persisted in market_data_sync_run."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.integrations.task_readers import normalize_execution
from lucking.models.market_data import MarketDataSyncAttempt, MarketDataSyncRun
from lucking.ports.task_execution_reader import TaskExecution
from lucking.task_catalog import ScheduledTask, tasks_due_on


def _utc_day_bounds(business_date: date) -> tuple[datetime, datetime]:
    timezone = ZoneInfo("Asia/Shanghai")
    start = datetime.combine(business_date, time.min, timezone).astimezone(UTC)
    end = datetime.combine(business_date, time.max, timezone).astimezone(UTC)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


class MarketDataTaskReader:
    def __init__(self, sessions: sessionmaker[Session], *, source_domains: frozenset[str]) -> None:
        self._sessions = sessions
        self._source_domains = source_domains

    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]:
        tasks = [
            task
            for task in tasks_due_on(business_date)
            if task.source_domain in self._source_domains
        ]
        return [self._read_task(task, business_date, observed_at) for task in tasks]

    def _read_task(
        self, task: ScheduledTask, business_date: date, observed_at: datetime
    ) -> TaskExecution:
        start, end = _utc_day_bounds(business_date)
        with self._sessions() as session:
            run = session.scalar(
                select(MarketDataSyncRun)
                .where(
                    MarketDataSyncRun.run_kind == "SCHEDULED",
                    MarketDataSyncRun.schedule_slug == task.schedule_slug,
                    MarketDataSyncRun.data_kind == task.data_kind,
                    MarketDataSyncRun.scheduled_for >= start,
                    MarketDataSyncRun.scheduled_for <= end,
                )
                .order_by(MarketDataSyncRun.scheduled_for.desc())
            )
            attempt = None
            if run is not None:
                attempt = session.scalar(
                    select(MarketDataSyncAttempt)
                    .where(MarketDataSyncAttempt.run_id == run.run_id)
                    .order_by(MarketDataSyncAttempt.attempt_no.desc())
                )
        return normalize_execution(
            task_key=task.task_key,
            schedule_slug=task.schedule_slug,
            display_name=task.display_name,
            source_domain=task.source_domain,
            business_date=business_date,
            observed_at=observed_at,
            source_status=run.status if run else None,
            has_quality_issues=bool(attempt and (attempt.invalid_count or attempt.conflict_count)),
            source_run_id=run.run_id if run else None,
            source_flow_run_id=attempt.flow_run_id if attempt else None,
            started_at=attempt.started_at if attempt else None,
            completed_at=attempt.completed_at if attempt else None,
            record_count=attempt.valid_count if attempt else None,
            error_category=attempt.error_category if attempt else None,
            error_summary=attempt.error_summary if attempt else None,
        )


def market_data_reader(sessions: sessionmaker[Session]) -> MarketDataTaskReader:
    return MarketDataTaskReader(sessions, source_domains=frozenset({"market-data"}))
