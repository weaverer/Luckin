from datetime import UTC, datetime

import pytest

from lucking.integrations.tushare.client import (
    TushareError,
    TushareErrorCategory,
    TushareTable,
)
from lucking.integrations.tushare.stock_list_provider import (
    STOCK_BASIC_FIELDS,
    TushareStockListProvider,
)
from lucking.ports.stock_list_provider import (
    ListingStatus,
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderIncompleteError,
    ScopeCode,
    StockListRequest,
    VenueCode,
)


class FakeClient:
    def __init__(self, rows_by_segment=None) -> None:
        self.calls = []
        self.rows_by_segment = rows_by_segment or {}

    def call(self, api_name, *, params, fields, allow_empty=False):
        self.calls.append((api_name, params, fields, allow_empty))
        rows = self.rows_by_segment.get((params["exchange"], params["list_status"]), ())
        return TushareTable(tuple(fields), tuple(rows))


def _row(exchange: str, status: str, symbol: str = "600000"):
    suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}[exchange]
    return {
        "ts_code": f"{symbol}.{suffix}",
        "symbol": symbol,
        "name": " 测试股票 ",
        "exchange": exchange,
        "curr_type": "CNY",
        "list_status": status,
        "list_date": "" if status == "G" else "20200102",
        "delist_date": "20250102" if status == "D" else "",
    }


def test_adapter_calls_only_stock_basic_for_fixed_twelve_segments() -> None:
    rows = {
        ("SSE", "L"): (_row("SSE", "L"),),
        ("SZSE", "D"): (_row("SZSE", "D", "000001"),),
        ("BSE", "P"): (_row("BSE", "P", "430001"),),
        ("BSE", "G"): (_row("BSE", "G", "430002"),),
    }
    client = FakeClient(rows)
    provider = TushareStockListProvider(
        client,
        now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
        monotonic=lambda: 10.0,
        sleep=lambda _: None,
    )
    result = provider.fetch_stock_list(
        StockListRequest(ScopeCode.CN_STOCK), deadline=100.0
    )

    assert len(client.calls) == 12
    assert {call[0] for call in client.calls} == {"stock_basic"}
    assert all(call[2] == STOCK_BASIC_FIELDS and call[3] is True for call in client.calls)
    assert all(set(call[1]) == {"exchange", "list_status"} for call in client.calls)
    assert result.evidence.segment_count == result.evidence.completed_segment_count == 12
    assert result.evidence.capped_segment_count == 0
    assert {record.venue_code for record in result.records} == {
        VenueCode.SHANGHAI,
        VenueCode.SHENZHEN,
        VenueCode.BEIJING,
    }
    assert {record.listing_status for record in result.records} == set(ListingStatus)
    assert result.records[0].display_name == "测试股票"


def test_empty_aggregate_and_capped_segment_fail() -> None:
    with pytest.raises(ProviderIncompleteError):
        TushareStockListProvider(
            FakeClient(),
            monotonic=lambda: 1.0,
            sleep=lambda _: None,
        ).fetch_stock_list(StockListRequest(), deadline=2.0)


def test_retryable_failure_retries_only_current_segment_with_bounded_backoff() -> None:
    events = []
    waits = []

    class RetryClient(FakeClient):
        def __init__(self):
            super().__init__({("SSE", "L"): (_row("SSE", "L"),)})
            self.failures = 0

        def call(self, api_name, *, params, fields, allow_empty=False):
            if params == {"exchange": "SSE", "list_status": "L"} and self.failures < 3:
                self.failures += 1
                raise TushareError(
                    TushareErrorCategory.RATE_LIMITED,
                    "上游短时频率限制",
                    status_code=429,
                )
            return super().call(
                api_name,
                params=params,
                fields=fields,
                allow_empty=allow_empty,
            )

    client = RetryClient()
    result = TushareStockListProvider(
        client,
        monotonic=lambda: 1.0,
        sleep=waits.append,
        event_sink=lambda event, **fields: events.append((event, fields)),
    ).fetch_stock_list(StockListRequest(), deadline=1000.0)
    assert result.evidence.completed_segment_count == 12
    assert waits == [30.0, 120.0, 300.0]
    assert client.failures == 3
    assert any(event == "stock_list_segment_attempt_failed" for event, _ in events)


def test_non_retryable_and_deadline_errors_fail_without_sleep() -> None:
    waits = []

    class AuthClient(FakeClient):
        def call(self, api_name, *, params, fields, allow_empty=False):
            raise TushareError(TushareErrorCategory.AUTHENTICATION, "凭据无效")

    with pytest.raises(ProviderAuthenticationError):
        TushareStockListProvider(
            AuthClient(),
            monotonic=lambda: 1.0,
            sleep=waits.append,
        ).fetch_stock_list(StockListRequest(), deadline=100.0)
    assert waits == []

    with pytest.raises(ProviderDeadlineExceededError):
        TushareStockListProvider(
            FakeClient(),
            monotonic=lambda: 100.0,
            sleep=waits.append,
        ).fetch_stock_list(StockListRequest(), deadline=100.0)


    rows = {("SSE", "L"): tuple(_row("SSE", "L") for _ in range(6000))}
    with pytest.raises(ProviderIncompleteError, match="6,000"):
        TushareStockListProvider(
            FakeClient(rows),
            monotonic=lambda: 1.0,
            sleep=lambda _: None,
        ).fetch_stock_list(StockListRequest(), deadline=2.0)
