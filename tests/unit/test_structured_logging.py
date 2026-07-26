import json
from datetime import UTC, datetime, timedelta

from lucking.logging import (
    JsonlLogStore,
    calculate_schedule_timing,
    calculate_timeliness_summary,
)


def test_schedule_timing_boundary_and_manual_exclusion() -> None:
    scheduled = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)
    started = scheduled + timedelta(minutes=1)
    completed = scheduled + timedelta(minutes=10)
    timing = calculate_schedule_timing(scheduled, started, completed)
    assert timing.schedule_delay_ms == 60_000
    assert timing.run_duration_ms == 540_000
    assert timing.schedule_to_completion_ms == 600_000
    assert timing.timeliness_met is True
    assert calculate_schedule_timing(None, started, completed).timeliness_met is None


def test_recent_twenty_summary_is_grouped_and_provisional() -> None:
    events = [
        {"schedule_slug": "monthly", "timeliness_met": index != 0, "completed_at": index}
        for index in range(21)
    ] + [{"schedule_slug": "year-end", "timeliness_met": True, "completed_at": 1}]
    monthly = calculate_timeliness_summary(events, "monthly")
    year_end = calculate_timeliness_summary(events, "year-end")
    assert monthly.sample_size == 20
    assert monthly.met_count == 20
    assert monthly.formal is True
    assert year_end.sample_size == 1
    assert year_end.formal is False


def test_jsonl_whitelist_rotation_and_redaction(tmp_path) -> None:
    store = JsonlLogStore(tmp_path, max_bytes=300, backup_count=2)
    for _ in range(12):
        store.write(
            "sync_failed",
            flow_run_id="run-1",
            error_category="AUTHENTICATION",
            error_summary="token=secret mysql+pymysql://user:password@host/db",
            forbidden="must-not-appear",
        )
    lines = [
        line
        for path in tmp_path.glob("trading-calendar-sync.jsonl*")
        for line in path.read_text().splitlines()
    ]
    assert lines
    assert all(json.loads(line)["flow_run_id"] == "run-1" for line in lines)
    content = "\n".join(lines)
    assert "must-not-appear" not in content
    assert "password" not in content
    assert "secret" not in content

