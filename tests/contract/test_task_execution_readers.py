from datetime import UTC, date, datetime

import pytest

from lucking.integrations.task_readers import normalize_execution
from lucking.ports.task_execution_reader import TaskExecutionStatus


@pytest.mark.parametrize(
    ("source_status", "has_quality_issues", "expected"),
    [
        ("SUCCEEDED", False, TaskExecutionStatus.SUCCEEDED),
        ("SUCCEEDED", True, TaskExecutionStatus.PARTIAL),
        ("FAILED", False, TaskExecutionStatus.FAILED),
        ("RUNNING", False, TaskExecutionStatus.RUNNING),
        ("SKIPPED", False, TaskExecutionStatus.UNKNOWN),
        (None, False, TaskExecutionStatus.NOT_RUN),
    ],
)
def test_reader_golden_status_mapping(
    source_status: str | None,
    has_quality_issues: bool,
    expected: TaskExecutionStatus,
) -> None:
    execution = normalize_execution(
        task_key="daily-quote-sync",
        schedule_slug="daily-quote-sync",
        display_name="日线行情同步",
        source_domain="market-data",
        business_date=date(2026, 8, 8),
        observed_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        source_status=source_status,
        has_quality_issues=has_quality_issues,
    )
    assert execution.status is expected
    if expected is TaskExecutionStatus.NOT_RUN:
        assert execution.source_run_id is None
        assert execution.started_at is None
        assert execution.completed_at is None
        assert execution.error_summary == "未发现该计划任务的权威运行记录"


def test_reader_never_leaks_provider_payload() -> None:
    execution = normalize_execution(
        task_key="stock-list-sync",
        schedule_slug="daily-stock-list",
        display_name="股票列表同步",
        source_domain="stock-list",
        business_date=date(2026, 8, 8),
        observed_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        source_status="FAILED",
        error_category="PROVIDER_AUTH",
        error_summary="安全摘要",
    )
    assert "provider_payload" not in execution.__dataclass_fields__
    assert execution.error_summary == "安全摘要"


def test_reader_interprets_database_naive_timestamps_as_utc() -> None:
    execution = normalize_execution(
        task_key="stock-list-sync",
        schedule_slug="daily-stock-list",
        display_name="股票列表同步",
        source_domain="stock-list",
        business_date=date(2026, 8, 8),
        observed_at=datetime(2026, 8, 8, 12),
        source_status="SUCCEEDED",
        started_at=datetime(2026, 8, 8, 1),
        completed_at=datetime(2026, 8, 8, 2),
    )
    assert execution.observed_at.tzinfo is UTC
    assert execution.started_at is not None
    assert execution.started_at.tzinfo is UTC
    assert execution.completed_at is not None
    assert execution.completed_at.tzinfo is UTC
