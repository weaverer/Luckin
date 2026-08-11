"""Immutable daily task snapshot generation and notification copy."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Protocol

from lucking.ports.notification_sender import (
    NotificationDisposition,
    NotificationMessage,
    NotificationResult,
    NotificationSender,
)
from lucking.ports.task_execution_reader import (
    TaskExecution,
    TaskExecutionReader,
    TaskExecutionStatus,
)
from lucking.task_catalog import tasks_due_on


@dataclass(frozen=True, slots=True)
class SummarySnapshot:
    business_date: date
    scheduled_for: datetime
    executions: tuple[TaskExecution, ...]
    counts: dict[TaskExecutionStatus, int]
    digest: str

    @property
    def total_count(self) -> int:
        return len(self.executions)


@dataclass(frozen=True, slots=True)
class StoredSummary:
    summary_id: str
    notification_status: str
    snapshot: SummarySnapshot
    status: str = "READY"
    generated_at: datetime | None = None
    notified_at: datetime | None = None
    latest_notification_attempt: "NotificationAttemptView | None" = None


@dataclass(frozen=True, slots=True)
class NotificationAttemptView:
    attempt_no: int
    trigger_kind: str
    status: str
    error_category: str | None
    error_summary: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class NotificationClaim:
    attempt_id: str
    summary: StoredSummary


class TaskSummaryRepository(Protocol):
    def create_or_get(self, snapshot: SummarySnapshot) -> StoredSummary: ...
    def get(self, summary_id: str) -> StoredSummary: ...
    def get_by_business_date(self, business_date: date) -> StoredSummary | None: ...
    def claim_notification(
        self, summary_id: str, *, manual_retry: bool
    ) -> NotificationClaim | None: ...
    def complete_notification(
        self, attempt_id: str, result: NotificationResult, completed_at: datetime
    ) -> None: ...


def snapshot_from_executions(
    business_date: date,
    scheduled_for: datetime,
    executions: list[TaskExecution],
) -> SummarySnapshot:
    ordered = tuple(sorted(executions, key=lambda item: item.task_key))
    if len({item.task_key for item in ordered}) != len(ordered):
        raise ValueError("任务快照包含重复 task_key")
    counts = {status: 0 for status in TaskExecutionStatus}
    for execution in ordered:
        counts[execution.status] += 1
    canonical = json.dumps(
        [
            {
                "task_key": item.task_key,
                "status": item.status.value,
                "source_run_id": item.source_run_id,
                "record_count": item.record_count,
                "error_category": item.error_category,
                "error_summary": item.error_summary,
            }
            for item in ordered
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SummarySnapshot(
        business_date,
        scheduled_for,
        ordered,
        counts,
        sha256(canonical.encode()).hexdigest(),
    )


def summary_message(snapshot: SummarySnapshot) -> NotificationMessage:
    counts = snapshot.counts
    lines = [
        f"统计日期：{snapshot.business_date.isoformat()}",
        f"总计 {snapshot.total_count}｜成功 {counts[TaskExecutionStatus.SUCCEEDED]}｜"
        f"部分完成 {counts[TaskExecutionStatus.PARTIAL]}｜"
        f"失败 {counts[TaskExecutionStatus.FAILED]}",
        f"运行中 {counts[TaskExecutionStatus.RUNNING]}｜未知 "
        f"{counts[TaskExecutionStatus.UNKNOWN]}｜未执行 {counts[TaskExecutionStatus.NOT_RUN]}",
    ]
    exceptions = [
        item
        for item in snapshot.executions
        if item.status
        in {
            TaskExecutionStatus.PARTIAL,
            TaskExecutionStatus.FAILED,
            TaskExecutionStatus.RUNNING,
            TaskExecutionStatus.UNKNOWN,
            TaskExecutionStatus.NOT_RUN,
        }
    ]
    if exceptions:
        lines.append("异常任务：")
        lines.extend(
            f"- {item.display_name}（{item.task_key}）：{item.status.value}" for item in exceptions
        )
    return NotificationMessage(
        title=f"Lucking 每日任务汇总 · {snapshot.business_date.isoformat()}",
        text="\n".join(lines),
        idempotency_key=snapshot.digest,
    )


class DailyTaskSummaryService:
    def __init__(
        self,
        repository: TaskSummaryRepository,
        readers: list[TaskExecutionReader],
        sender: NotificationSender,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._readers = readers
        self._sender = sender
        self._now = now or (lambda: datetime.now(UTC))

    def build(self, business_date: date, scheduled_for: datetime) -> StoredSummary:
        executions = observe_executions(self._readers, business_date, scheduled_for)
        return self._repository.create_or_get(
            snapshot_from_executions(business_date, scheduled_for, executions)
        )

    def notify(self, summary_id: str, *, manual_retry: bool = False) -> NotificationResult | None:
        claim = self._repository.claim_notification(summary_id, manual_retry=manual_retry)
        if claim is None:
            return None
        result = self._sender.send(summary_message(claim.summary.snapshot))
        self._repository.complete_notification(claim.attempt_id, result, self._now())
        return result

    def run(self, business_date: date, scheduled_for: datetime) -> StoredSummary:
        summary = self.build(business_date, scheduled_for)
        result = self.notify(summary.summary_id)
        if result and result.disposition is NotificationDisposition.RETRYABLE_FAILURE:
            raise RuntimeError("通知暂时失败，可安全重试")
        return self._repository.get(summary.summary_id)


def observe_executions(
    readers: list[TaskExecutionReader], business_date: date, observed_at: datetime
) -> list[TaskExecution]:
    executions = [
        execution for reader in readers for execution in reader.read(business_date, observed_at)
    ]
    by_key = {execution.task_key: execution for execution in executions}
    for task in tasks_due_on(business_date):
        by_key.setdefault(
            task.task_key,
            TaskExecution(
                task_key=task.task_key,
                schedule_slug=task.schedule_slug,
                display_name=task.display_name,
                source_domain=task.source_domain,
                business_date=business_date,
                status=TaskExecutionStatus.NOT_RUN,
                observed_at=observed_at,
                error_category="NOT_RUN",
                error_summary="未发现该计划任务的权威运行记录",
            ),
        )
    return sorted(by_key.values(), key=lambda item: item.task_key)


class DailyTaskStatusQuery:
    def __init__(
        self, repository: TaskSummaryRepository, readers: list[TaskExecutionReader]
    ) -> None:
        self._repository = repository
        self._readers = readers

    def live(self, business_date: date, observed_at: datetime) -> SummarySnapshot:
        return snapshot_from_executions(
            business_date,
            observed_at,
            observe_executions(self._readers, business_date, observed_at),
        )

    def history(self, business_date: date) -> StoredSummary | None:
        return self._repository.get_by_business_date(business_date)
