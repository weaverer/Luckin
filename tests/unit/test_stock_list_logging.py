from datetime import UTC, datetime, timedelta

from lucking.logging import (
    JsonlLogStore,
    calculate_schedule_timing,
    calculate_timeliness_summary,
    safe_identifier_hash,
)


def test_log_store_filename_fields_and_rotation_are_configurable(tmp_path) -> None:
    store = JsonlLogStore(
        tmp_path,
        filename="stock-list-sync.jsonl",
        allowed_fields={"run_id", "error_summary"},
        max_bytes=200,
        backup_count=2,
    )
    for _ in range(10):
        store.write(
            "stock_list_sync_failed",
            run_id="run-1",
            error_summary="token=super-secret",
            forbidden="raw-row",
        )
    assert list(tmp_path.glob("stock-list-sync.jsonl*"))
    content = "\n".join(path.read_text() for path in tmp_path.glob("*.jsonl*"))
    assert "run-1" in content
    assert "raw-row" not in content
    assert "super-secret" not in content


def test_timing_target_and_recent_planned_sample_are_configurable() -> None:
    scheduled = datetime(2026, 7, 1, tzinfo=UTC)
    timing = calculate_schedule_timing(
        scheduled,
        scheduled + timedelta(minutes=2),
        scheduled + timedelta(minutes=30),
        target_ms=1_800_000,
    )
    assert timing.timeliness_met is True

    events = [
        {
            "schedule_slug": "daily-stock-list",
            "timeliness_met": index != 0,
            "completed_at": index,
        }
        for index in range(31)
    ] + [
        {
            "schedule_slug": "manual-stock-list",
            "timeliness_met": False,
            "completed_at": 100,
        }
    ]
    summary = calculate_timeliness_summary(
        events,
        "daily-stock-list",
        sample_limit=30,
        required_met=30,
    )
    assert summary.sample_size == 30
    assert summary.met_count == 30
    assert summary.formal is True


def test_identifier_hash_is_deterministic_and_does_not_expose_source() -> None:
    value = safe_identifier_hash("600000.SH")
    assert len(value) == 64
    assert value == safe_identifier_hash("600000.SH")
    assert "600000.SH" not in value
