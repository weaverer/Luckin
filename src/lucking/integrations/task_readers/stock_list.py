"""Stock-list scheduled-run reader."""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.integrations.task_readers import normalize_execution
from lucking.models.stock_list import StockListSyncRun
from lucking.ports.task_execution_reader import TaskExecution
from lucking.task_catalog import tasks_due_on


class StockListTaskReader:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]:
        tasks = [task for task in tasks_due_on(business_date) if task.source_domain == "stock-list"]
        if not tasks:
            return []
        task = tasks[0]
        with self._sessions() as session:
            row = session.scalar(
                select(StockListSyncRun)
                .where(
                    StockListSyncRun.business_date == business_date,
                    StockListSyncRun.schedule_slug == task.schedule_slug,
                )
                .order_by(StockListSyncRun.scheduled_for.desc())
            )
        return [
            normalize_execution(
                task_key=task.task_key,
                schedule_slug=task.schedule_slug,
                display_name=task.display_name,
                source_domain=task.source_domain,
                business_date=business_date,
                observed_at=observed_at,
                source_status=row.status if row else None,
                has_quality_issues=bool(
                    row and (row.invalid_count or row.conflict_count or row.capped_segment_count)
                ),
                source_run_id=row.run_id if row else None,
                source_flow_run_id=row.flow_run_id if row else None,
                started_at=row.started_at if row else None,
                completed_at=row.completed_at if row else None,
                record_count=row.valid_count if row else None,
                error_category=row.error_category if row else None,
                error_summary=row.error_summary if row else None,
            )
        ]
