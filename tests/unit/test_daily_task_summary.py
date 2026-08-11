from datetime import UTC, date, datetime

from lucking.ports.task_execution_reader import TaskExecution, TaskExecutionStatus
from lucking.services.daily_task_summary import snapshot_from_executions, summary_message


def execution(status: TaskExecutionStatus, key: str = "daily-quote") -> TaskExecution:
    return TaskExecution(
        task_key=key,
        schedule_slug=key,
        display_name=key,
        source_domain="market-data",
        business_date=date(2026, 8, 8),
        status=status,
        observed_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )


def test_snapshot_counts_each_task_in_exactly_one_of_six_states() -> None:
    statuses = list(TaskExecutionStatus)
    snapshot = snapshot_from_executions(
        date(2026, 8, 8),
        datetime(2026, 8, 8, 12, tzinfo=UTC),
        [execution(status, status.value) for status in statuses],
    )
    assert snapshot.total_count == 6
    assert sum(snapshot.counts.values()) == 6
    assert all(snapshot.counts[status] == 1 for status in statuses)
    assert len(snapshot.digest) == 64


def test_message_reports_incomplete_and_exception_tasks() -> None:
    snapshot = snapshot_from_executions(
        date(2026, 8, 8),
        datetime(2026, 8, 8, 12, tzinfo=UTC),
        [execution(TaskExecutionStatus.FAILED, "failed-task")],
    )
    message = summary_message(snapshot)
    assert "2026-08-08" in message.text
    assert "失败 1" in message.text
    assert "failed-task" in message.text
    assert message.idempotency_key == snapshot.digest


def test_message_reports_unknown_instead_of_skipped() -> None:
    snapshot = snapshot_from_executions(
        date(2026, 8, 8),
        datetime(2026, 8, 8, 12, tzinfo=UTC),
        [execution(TaskExecutionStatus.UNKNOWN, "unknown-task")],
    )
    message = summary_message(snapshot)
    assert "未知 1" in message.text
    assert "unknown-task" in message.text
