"""Trading-calendar execution reader backed by current-value update evidence."""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.integrations.task_readers import normalize_execution
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.task_execution_reader import TaskExecution
from lucking.task_catalog import tasks_due_on


class TradingCalendarTaskReader:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]:
        tasks = [
            task for task in tasks_due_on(business_date) if task.source_domain == "trading-calendar"
        ]
        if not tasks:
            return []
        with self._sessions() as session:
            updated_at = session.scalar(
                select(func.max(TradingCalendar.updated_at)).where(
                    TradingCalendar.market_code == "CN-S"
                )
            )
        succeeded = updated_at is not None and updated_at.date() == business_date
        return [
            normalize_execution(
                task_key=task.task_key,
                schedule_slug=task.schedule_slug,
                display_name=task.display_name,
                source_domain=task.source_domain,
                business_date=business_date,
                observed_at=observed_at,
                source_status="SUCCEEDED" if succeeded else None,
                completed_at=updated_at if succeeded else None,
            )
            for task in tasks
        ]
