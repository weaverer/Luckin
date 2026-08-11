"""Shared task-reader normalization and registration helpers."""

from datetime import date, datetime

from lucking.db import as_utc_aware
from lucking.ports.task_execution_reader import TaskExecution, TaskExecutionStatus

_STATUS_MAP = {
    "SUCCEEDED": TaskExecutionStatus.SUCCEEDED,
    "FAILED": TaskExecutionStatus.FAILED,
    "RUNNING": TaskExecutionStatus.RUNNING,
    "PENDING": TaskExecutionStatus.RUNNING,
    "SKIPPED": TaskExecutionStatus.UNKNOWN,
}


def normalize_execution(
    *,
    task_key: str,
    schedule_slug: str,
    display_name: str,
    source_domain: str,
    business_date: date,
    observed_at: datetime,
    source_status: str | None,
    has_quality_issues: bool = False,
    source_run_id: str | None = None,
    source_flow_run_id: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    record_count: int | None = None,
    error_category: str | None = None,
    error_summary: str | None = None,
) -> TaskExecution:
    status = _STATUS_MAP.get(source_status or "", TaskExecutionStatus.NOT_RUN)
    if status is TaskExecutionStatus.SUCCEEDED and has_quality_issues:
        status = TaskExecutionStatus.PARTIAL
    if status is TaskExecutionStatus.NOT_RUN:
        source_run_id = None
        source_flow_run_id = None
        started_at = None
        completed_at = None
        record_count = None
        error_category = "NOT_RUN"
        error_summary = "未发现该计划任务的权威运行记录"
    elif status is TaskExecutionStatus.UNKNOWN:
        error_category = error_category or "STATUS_UNKNOWN"
        error_summary = error_summary or "来源任务标记为跳过，结果状态待确认"
    return TaskExecution(
        task_key=task_key,
        schedule_slug=schedule_slug,
        display_name=display_name,
        source_domain=source_domain,
        business_date=business_date,
        status=status,
        observed_at=as_utc_aware(observed_at),
        source_run_id=source_run_id,
        source_flow_run_id=source_flow_run_id,
        started_at=as_utc_aware(started_at) if started_at else None,
        completed_at=as_utc_aware(completed_at) if completed_at else None,
        record_count=record_count,
        error_category=error_category[:64] if error_category else None,
        error_summary=error_summary[:500] if error_summary else None,
    )
