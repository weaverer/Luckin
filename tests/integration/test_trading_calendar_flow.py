from datetime import date

import pytest

from lucking.flows.trading_calendar import (
    RetryPolicy,
    execute_with_retry,
    resolve_sync_window,
)
from lucking.ports.trading_calendar_provider import (
    ProviderAuthenticationError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    SyncMode,
)
from lucking.services.trading_calendar import CompletenessStatus, InvalidSyncRequest, SyncResult


@pytest.mark.parametrize(
    "error",
    [
        ProviderQuotaExceededError("test", "quota"),
        ProviderAuthenticationError("test", "auth"),
        InvalidSyncRequest("invalid"),
    ],
)
def test_non_retryable_errors_fail_immediately(error: Exception) -> None:
    attempts = 0

    def operation():
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(type(error)):
        execute_with_retry(operation, sleep=lambda _: None)
    assert attempts == 1


@pytest.mark.parametrize(
    "error",
    [
        ProviderRateLimitedError("test", "rate"),
        ProviderUnavailableError("test", "unavailable"),
    ],
)
def test_retryable_errors_use_bounded_backoff(error: Exception) -> None:
    attempts = 0
    waits = []

    def operation():
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise error
        return "ok"

    assert execute_with_retry(operation, sleep=waits.append) == "ok"
    assert attempts == 4
    assert waits == list(RetryPolicy.delays)


def test_manual_range_validation_happens_before_provider() -> None:
    with pytest.raises(InvalidSyncRequest):
        resolve_sync_window(
            SyncMode.MANUAL,
            as_of_date=date(2026, 7, 26),
            start_date=date(2026, 7, 27),
            end_date=date(2026, 7, 26),
        )


def test_manual_flow_writes_result_log_without_schedule_timing(monkeypatch, tmp_path) -> None:
    class FakeService:
        provider_code = "memory"

        def sync_range(self, mode, market, start, end, as_of):
            return SyncResult(
                source="memory",
                sync_mode=mode,
                market_code=market,
                start_date=start,
                end_date=end,
                coverage_end=end,
                completeness_status=CompletenessStatus.COMPLETE,
                missing_future_count=0,
                received_count=2,
                written_count=2,
            )

    monkeypatch.setenv("TRADING_CALENDAR_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "lucking.flows.trading_calendar._build_service", lambda _: FakeService()
    )
    from lucking.flows.trading_calendar import sync_trading_calendar

    result = sync_trading_calendar.fn(
        mode="manual",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        as_of_date=date(2026, 7, 2),
    )
    assert result["status"] == "SUCCEEDED"
    content = (tmp_path / "trading-calendar-sync.jsonl").read_text()
    assert '"timeliness_met":null' in content
    assert '"sync_mode":"manual"' in content
