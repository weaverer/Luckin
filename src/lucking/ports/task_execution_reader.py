"""Provider-neutral task execution snapshot port."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol


class TaskExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True, slots=True)
class TaskExecution:
    task_key: str
    schedule_slug: str
    display_name: str
    source_domain: str
    business_date: date
    status: TaskExecutionStatus
    observed_at: datetime
    source_run_id: str | None = None
    source_flow_run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    record_count: int | None = None
    error_category: str | None = None
    error_summary: str | None = None


class TaskExecutionReader(Protocol):
    def read(self, business_date: date, observed_at: datetime) -> list[TaskExecution]: ...
