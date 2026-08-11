"""Whitelisted JSONL synchronization logs and timing calculations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_ALLOWED_FIELDS = {
    "timestamp",
    "level",
    "event",
    "flow_run_id",
    "schedule_slug",
    "source",
    "sync_mode",
    "market_code",
    "start_date",
    "end_date",
    "scheduled_at",
    "started_at",
    "completed_at",
    "schedule_delay_ms",
    "run_duration_ms",
    "schedule_to_completion_ms",
    "timeliness_met",
    "coverage_end",
    "completeness_status",
    "missing_future_count",
    "received_count",
    "written_count",
    "attempt",
    "error_category",
    "error_summary",
    "timeliness_sample_size",
    "timeliness_met_count",
    "timeliness_rate",
    "timeliness_formal",
    "run_id",
    "run_key",
    "attempt_count",
    "provider_code",
    "scope_code",
    "business_date",
    "published_at",
    "segment_count",
    "completed_segment_count",
    "capped_segment_count",
    "valid_count",
    "duplicate_count",
    "invalid_count",
    "conflict_count",
    "added_count",
    "updated_count",
    "unchanged_count",
    "attempt_id",
    "attempt_no",
    "run_kind",
    "backfill_batch_id",
    "target_month",
    "provider_request_count",
    "provider_retry_count",
    "provider_page_count",
    "provider_page_limit",
    "provider_last_page_count",
    "timeliness_target_ms",
    "duration_ms",
    "schedule_lag_ms",
    "completed_after_schedule_ms",
    "data_kind",
    "target_trade_date",
    "window_timeliness",
    "status",
    "skipped",
    "request_id",
    "summary_id",
    "task_key",
    "notification_attempt_id",
    "method",
    "path",
    "http_status",
}


@dataclass(frozen=True, slots=True)
class ScheduleTiming:
    schedule_delay_ms: int | None
    run_duration_ms: int | None
    schedule_to_completion_ms: int | None
    timeliness_met: bool | None


@dataclass(frozen=True, slots=True)
class TimelinessSummary:
    schedule_slug: str
    sample_size: int
    met_count: int
    rate: float | None
    formal: bool
    target_met: bool | None


def calculate_schedule_timing(
    scheduled_at: datetime | None,
    started_at: datetime,
    completed_at: datetime,
    target_ms: int = 600_000,
) -> ScheduleTiming:
    if scheduled_at is None:
        return ScheduleTiming(None, None, None, None)
    delay = _milliseconds(started_at - scheduled_at)
    duration = _milliseconds(completed_at - started_at)
    total = _milliseconds(completed_at - scheduled_at)
    return ScheduleTiming(delay, duration, total, total <= target_ms)


def calculate_timeliness_summary(
    events: Iterable[dict[str, Any]],
    schedule_slug: str,
    *,
    sample_limit: int = 20,
    required_met: int = 19,
) -> TimelinessSummary:
    candidates = [
        event
        for event in events
        if event.get("schedule_slug") == schedule_slug
        and isinstance(event.get("timeliness_met"), bool)
    ]
    candidates.sort(key=lambda event: str(event.get("completed_at", "")), reverse=True)
    recent = candidates[:sample_limit]
    sample_size = len(recent)
    met_count = sum(event["timeliness_met"] is True for event in recent)
    rate = met_count / sample_size if sample_size else None
    formal = sample_size == sample_limit
    return TimelinessSummary(
        schedule_slug,
        sample_size,
        met_count,
        rate,
        formal,
        (met_count >= required_met) if formal else None,
    )


class JsonlLogStore:
    def __init__(
        self,
        directory: Path,
        *,
        filename: str = "trading-calendar-sync.jsonl",
        allowed_fields: set[str] | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        self.directory = Path(directory)
        self.filename = filename
        self.allowed_fields = _ALLOWED_FIELDS if allowed_fields is None else allowed_fields
        self.max_bytes = max_bytes
        self.backup_count = backup_count

    @property
    def path(self) -> Path:
        return self.directory / self.filename

    def write(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": level,
            "event": event,
        }
        for key, value in fields.items():
            if key in self.allowed_fields:
                entry[key] = _json_value(_redact(value))
        encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        if self.path.exists() and self.path.stat().st_size + len(encoded.encode()) > self.max_bytes:
            self._rotate()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(encoded)

    def read_events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        paths = [
            self.path.with_name(f"{self.filename}.{index}")
            for index in range(self.backup_count, 0, -1)
        ]
        paths.append(self.path)
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    result.append(value)
        return result

    def _rotate(self) -> None:
        if self.backup_count <= 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.filename}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.filename}.{index}")
            if source.exists():
                source.rename(self.path.with_name(f"{self.filename}.{index + 1}"))
        if self.path.exists():
            self.path.rename(self.path.with_name(f"{self.filename}.1"))


def _milliseconds(delta: Any) -> int:
    return int(delta.total_seconds() * 1000)


def safe_identifier_hash(value: str) -> str:
    """Return a stable one-way identifier for issue correlation."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = re.sub(
        r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+",
        "[REDACTED_WEBHOOK]",
        value,
    )
    value = re.sub(r"(?i)(token|password|secret)\s*[=:]\s*[^\s]+", r"\1=[REDACTED]", value)
    value = re.sub(
        r"(?i)\b(?:mysql(?:\+pymysql)?|postgresql|redis)://[^\s]+",
        "[REDACTED_CONNECTION]",
        value,
    )
    value = re.sub(
        r"(?is)(provider[_ -]?(?:raw )?response|raw[_ -]?response)\s*[=:].*",
        r"\1=[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?is)(traceback \(most recent call last\):|(?:select|insert|update|delete)\s+).*",
        "[REDACTED_DIAGNOSTIC]",
        value,
    )
    return value[:500]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
