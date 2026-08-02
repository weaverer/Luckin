"""market-data JSONL 日志字段白名单与窗口及时性测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lucking.logging import JsonlLogStore, calculate_schedule_timing
from lucking.models.market_data import DataKind


def test_market_data_fields_are_whitelisted(tmp_path: Path) -> None:
    store = JsonlLogStore(tmp_path, filename="market-data-sync.jsonl")
    store.write(
        "market_data_sync_succeeded",
        data_kind=DataKind.DAILY_QUOTE.value,
        target_trade_date=date(2026, 7, 27),
        run_id="run-1",
        attempt_id="attempt-1",
        run_kind="SCHEDULED",
        backfill_batch_id=None,
        received_count=5400,
        valid_count=5400,
        provider_retry_count=0,
        window_timeliness=True,
        skipped=False,
        token="should-not-appear",
    )
    events = store.read_events()
    assert len(events) == 1
    event = events[0]
    assert event["data_kind"] == "DAILY_QUOTE"
    assert event["target_trade_date"] == "2026-07-27"
    assert event["received_count"] == 5400
    assert event["window_timeliness"] is True
    assert event["skipped"] is False
    assert "token" not in event


def test_market_data_skip_event_carries_skipped_flag(tmp_path: Path) -> None:
    store = JsonlLogStore(tmp_path, filename="market-data-sync.jsonl")
    store.write(
        "market_data_sync_skipped",
        data_kind=DataKind.ADJ_FACTOR.value,
        target_trade_date=date(2026, 7, 25),
        skipped=True,
        status="SKIPPED",
    )
    event = store.read_events()[0]
    assert event["skipped"] is True
    assert event["status"] == "SKIPPED"


def test_market_data_log_rotation_keeps_five_archives(tmp_path: Path) -> None:
    store = JsonlLogStore(tmp_path, filename="market-data-sync.jsonl", max_bytes=512)
    for index in range(50):
        store.write(
            "market_data_sync_succeeded",
            data_kind=DataKind.DAILY_QUOTE.value,
            target_trade_date=date(2026, 7, 27),
            received_count=index,
        )
    archives = sorted(tmp_path.glob("market-data-sync.jsonl.*"))
    assert len(archives) == 5
    assert all(event["received_count"] >= 0 for event in store.read_events())


def test_adj_factor_window_timeliness_target_is_pre_open() -> None:
    scheduled = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)  # 上海 09:00
    started = scheduled + timedelta(seconds=30)
    completed = scheduled + timedelta(minutes=18)
    timing = calculate_schedule_timing(scheduled, started, completed, target_ms=20 * 60 * 1000)
    assert timing.timeliness_met is True
    late = calculate_schedule_timing(
        scheduled, started, scheduled + timedelta(minutes=25), target_ms=20 * 60 * 1000
    )
    assert late.timeliness_met is False
