"""Broker-recommendation scheduled-run reader."""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.integrations.task_readers import normalize_execution
from lucking.integrations.task_readers.market_data import _utc_day_bounds
from lucking.models.broker_recommendation import (
    BrokerRecommendationSyncAttempt,
    BrokerRecommendationSyncRun,
)
from lucking.ports.task_execution_reader import TaskExecution
from lucking.task_catalog import tasks_due_on


class BrokerRecommendationTaskReader:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]:
        tasks = [
            task
            for task in tasks_due_on(business_date)
            if task.source_domain == "broker-recommendation"
        ]
        if not tasks:
            return []
        task = tasks[0]
        start, end = _utc_day_bounds(business_date)
        with self._sessions() as session:
            run = session.scalar(
                select(BrokerRecommendationSyncRun)
                .where(
                    BrokerRecommendationSyncRun.run_kind == "SCHEDULED",
                    BrokerRecommendationSyncRun.schedule_slug == task.schedule_slug,
                    BrokerRecommendationSyncRun.scheduled_for >= start,
                    BrokerRecommendationSyncRun.scheduled_for <= end,
                )
                .order_by(BrokerRecommendationSyncRun.scheduled_for.desc())
            )
            attempt = None
            if run is not None:
                attempt = session.scalar(
                    select(BrokerRecommendationSyncAttempt)
                    .where(BrokerRecommendationSyncAttempt.run_id == run.run_id)
                    .order_by(BrokerRecommendationSyncAttempt.attempt_no.desc())
                )
        return [
            normalize_execution(
                task_key=task.task_key,
                schedule_slug=task.schedule_slug,
                display_name=task.display_name,
                source_domain=task.source_domain,
                business_date=business_date,
                observed_at=observed_at,
                source_status=run.status if run else None,
                has_quality_issues=bool(
                    attempt and (attempt.invalid_count or attempt.conflict_count)
                ),
                source_run_id=run.run_id if run else None,
                source_flow_run_id=attempt.flow_run_id if attempt else None,
                started_at=attempt.started_at if attempt else None,
                completed_at=attempt.completed_at if attempt else None,
                record_count=attempt.valid_count if attempt else None,
                error_category=attempt.error_category if attempt else None,
                error_summary=attempt.error_summary if attempt else None,
            )
        ]
