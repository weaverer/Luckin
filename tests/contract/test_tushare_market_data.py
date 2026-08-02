"""Tushare daily/adj_factor Adapter 契约测试（HTTPX MockTransport 与 FakeClient）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from lucking.integrations.tushare.adj_factor_provider import (
    ADJ_FACTOR_FIELDS,
    TushareAdjFactorProvider,
)
from lucking.integrations.tushare.client import TushareError, TushareErrorCategory, TushareTable
from lucking.integrations.tushare.daily_basic_provider import (
    DAILY_BASIC_FIELDS,
    TushareDailyBasicProvider,
)
from lucking.integrations.tushare.daily_quote_provider import (
    DAILY_QUOTE_FIELDS,
    TushareDailyQuoteProvider,
)
from lucking.integrations.tushare.weekly_monthly_kline_provider import (
    KLINE_FIELDS,
    TushareWeeklyMonthlyKlineProvider,
)
from lucking.models.market_data import VenueCode
from lucking.ports.adj_factor_provider import AdjFactorRequest
from lucking.ports.daily_basic_provider import DailyBasicRequest
from lucking.ports.daily_quote_provider import DailyQuoteRequest
from lucking.ports.market_data_common import (
    ProviderEmptyAggregateError,
    ProviderError,
    ProviderPayloadError,
    ProviderResponseCappedError,
)
from lucking.ports.weekly_monthly_kline_provider import KlineFreq, KlineRequest

_TARGET = date(2026, 7, 27)


class FakeClient:
    def __init__(self, pages: list[list[list[str]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any], tuple[str, ...]]] = []

    def call(
        self,
        api_name: str,
        *,
        params: dict[str, Any],
        fields: tuple[str, ...],
        allow_empty: bool = False,
    ) -> TushareTable:
        self.calls.append((api_name, params, fields))
        items = self.pages.pop(0)
        return TushareTable(
            fields,
            tuple(dict(zip(fields, row, strict=True)) for row in items),
        )


def _quote_rows(start: int, count: int, suffix: str = ".SH") -> list[list[str]]:
    return [
        [
            f"{index + 1:06d}{suffix}",
            "20260727",
            "10.0000",
            "11.0000",
            "9.5000",
            "10.5000",
            "10.0000",
            "0.5000",
            "5.000",
            "123456.00",
            "1234567.00",
        ]
        for index in range(start, start + count)
    ]


def _factor_rows(start: int, count: int, suffix: str = ".SH") -> list[list[str]]:
    return [
        [f"{index + 1:06d}{suffix}", "20260727", "1.234567"]
        for index in range(start, start + count)
    ]


def test_daily_quote_adapter_only_calls_daily_with_trade_date() -> None:
    client = FakeClient([_quote_rows(0, 2, ".BJ")])
    batch = TushareDailyQuoteProvider(client, monotonic=lambda: 0.0).fetch_daily_quotes(
        DailyQuoteRequest(_TARGET), deadline=10
    )
    assert client.calls == [("daily", {"trade_date": "20260727"}, DAILY_QUOTE_FIELDS)]
    assert batch.records[0].venue_code is VenueCode.BEIJING
    assert batch.records[0].security_code == "000001"
    assert batch.records[0].vol == 123456
    assert batch.records[0].amount == 1234567


def test_daily_quote_adapter_full_page_unverified_fails_capped() -> None:
    client = FakeClient([_quote_rows(0, 6000)])
    with pytest.raises(ProviderResponseCappedError):
        TushareDailyQuoteProvider(client, monotonic=lambda: 0.0).fetch_daily_quotes(
            DailyQuoteRequest(_TARGET), deadline=10
        )


def test_daily_quote_adapter_empty_response_fails_empty_aggregate() -> None:
    with pytest.raises(ProviderEmptyAggregateError):
        TushareDailyQuoteProvider(FakeClient([[]]), monotonic=lambda: 0.0).fetch_daily_quotes(
            DailyQuoteRequest(_TARGET), deadline=10
        )


def test_daily_quote_adapter_unknown_suffix_fails_whole_batch() -> None:
    rows = _quote_rows(0, 1)
    rows[0][0] = "000001.XX"
    with pytest.raises(ProviderPayloadError):
        TushareDailyQuoteProvider(FakeClient([rows]), monotonic=lambda: 0.0).fetch_daily_quotes(
            DailyQuoteRequest(_TARGET), deadline=10
        )


def test_daily_quote_adapter_mismatched_trade_date_fails_whole_batch() -> None:
    rows = _quote_rows(0, 1)
    rows[0][1] = "20260724"
    with pytest.raises(ProviderPayloadError) as excinfo:
        TushareDailyQuoteProvider(FakeClient([rows]), monotonic=lambda: 0.0).fetch_daily_quotes(
            DailyQuoteRequest(_TARGET), deadline=10
        )
    assert "交易日" in str(excinfo.value)


def test_adj_factor_adapter_isolates_non_positive_factor() -> None:
    rows = _factor_rows(0, 2)
    rows[1][2] = "0.000000"
    batch = TushareAdjFactorProvider(FakeClient([rows]), monotonic=lambda: 0.0).fetch_adj_factors(
        AdjFactorRequest(_TARGET), deadline=10
    )
    assert len(batch.records) == 1
    assert len(batch.isolated) == 1
    assert batch.isolated[0].category == "INVALID_FIELD"
    assert batch.evidence.received_count == 2


def test_adj_factor_adapter_uses_trade_date_and_three_fields() -> None:
    client = FakeClient([_factor_rows(0, 1)])
    batch = TushareAdjFactorProvider(client, monotonic=lambda: 0.0).fetch_adj_factors(
        AdjFactorRequest(_TARGET), deadline=10
    )
    assert client.calls == [("adj_factor", {"trade_date": "20260727"}, ADJ_FACTOR_FIELDS)]
    assert batch.records[0].adj_factor == Decimal("1.234567")


def test_transient_retry_is_bounded_to_three_and_backs_off() -> None:
    class RetryClient(FakeClient):
        def __init__(self) -> None:
            super().__init__([_quote_rows(0, 1)])
            self.failures = 3

        def call(
            self,
            api_name: str,
            *,
            params: dict[str, Any],
            fields: tuple[str, ...],
            allow_empty: bool = False,
        ) -> TushareTable:
            if self.failures:
                self.failures -= 1
                raise TushareError(TushareErrorCategory.NETWORK, "网络错误")
            return super().call(api_name, params=params, fields=fields, allow_empty=allow_empty)

    delays: list[float] = []
    batch = TushareDailyQuoteProvider(
        RetryClient(), monotonic=lambda: 0.0, sleep=delays.append
    ).fetch_daily_quotes(DailyQuoteRequest(_TARGET), deadline=1000)
    assert delays == [30.0, 120.0, 300.0]
    assert batch.evidence.retry_count == 3
    assert batch.evidence.request_count == 4


def test_deterministic_errors_are_not_retried() -> None:
    class AuthClient(FakeClient):
        def __init__(self) -> None:
            super().__init__([])
            self.failures = 1

        def call(
            self,
            api_name: str,
            *,
            params: dict[str, Any],
            fields: tuple[str, ...],
            allow_empty: bool = False,
        ) -> TushareTable:
            if self.failures:
                self.failures -= 1
                raise TushareError(TushareErrorCategory.AUTHENTICATION, "凭据无效")
            return super().call(api_name, params=params, fields=fields, allow_empty=allow_empty)

    with pytest.raises(ProviderError) as excinfo:
        TushareDailyQuoteProvider(AuthClient(), monotonic=lambda: 0.0).fetch_daily_quotes(
            DailyQuoteRequest(_TARGET), deadline=1000
        )
    assert excinfo.value.category == "AUTHENTICATION"


def _basic_rows(start: int, count: int, *, loss_making: bool = False) -> list[list[str]]:
    return [
        [
            f"{index + 1:06d}.SH",
            "20260727",
            "" if loss_making else "15.2500",
            "" if loss_making else "14.1000",
            "" if loss_making else "1.5000",
            "1.2000",
            "1.1000",
            "2.5000",
            "2.6000",
            "100000.0000",
            "80000.0000",
            "70000.0000",
            "1000000.0000",
            "800000.0000",
            "1.5000",
            "1.7000",
            "1.2000",
            "0",
        ]
        for index in range(start, start + count)
    ]


def test_daily_basic_adapter_uses_trade_date_without_close_field() -> None:
    client = FakeClient([_basic_rows(0, 1)])
    batch = TushareDailyBasicProvider(client, monotonic=lambda: 0.0).fetch_daily_basics(
        DailyBasicRequest(_TARGET), deadline=10
    )
    assert client.calls == [("daily_basic", {"trade_date": "20260727"}, DAILY_BASIC_FIELDS)]
    assert "close" not in DAILY_BASIC_FIELDS
    assert batch.records[0].pe == Decimal("15.2500")
    assert batch.records[0].limit_status == 0


def test_daily_basic_adapter_keeps_loss_making_nulls() -> None:
    provider = TushareDailyBasicProvider(
        FakeClient([_basic_rows(0, 1, loss_making=True)]), monotonic=lambda: 0.0
    )
    batch = provider.fetch_daily_basics(DailyBasicRequest(_TARGET), deadline=10)
    assert batch.records[0].pe is None
    assert batch.records[0].pb is None
    assert batch.records[0].turnover_rate == Decimal("1.5000")


def test_daily_basic_adapter_isolates_missing_identity_fields() -> None:
    rows = _basic_rows(0, 2)
    rows[1][0] = ""  # ts_code 缺失
    batch = TushareDailyBasicProvider(FakeClient([rows]), monotonic=lambda: 0.0).fetch_daily_basics(
        DailyBasicRequest(_TARGET), deadline=10
    )
    assert len(batch.records) == 1
    assert len(batch.isolated) == 1
    assert batch.isolated[0].category == "INVALID_FIELD"
    assert batch.evidence.received_count == 2


def _kline_rows(
    start: int, count: int, *, freq: str = "week", missing_price: bool = False
) -> list[list[str]]:
    return [
        [
            f"{index + 1:06d}.SH",
            "20260724",
            freq,
            "" if missing_price and index == start else "10.0000",
            "11.0000",
            "9.5000",
            "10.5000",
            "123456.00",
            "1234567.00",
            "0.5000",
            "5.000",
            "",
        ]
        for index in range(start, start + count)
    ]


def test_kline_adapter_dispatches_freq_param_and_maps_period() -> None:
    client = FakeClient([_kline_rows(0, 1, freq="week")])
    batch = TushareWeeklyMonthlyKlineProvider(client, monotonic=lambda: 0.0).fetch_kline(
        KlineRequest(KlineFreq.WEEK, _TARGET), deadline=10
    )
    assert client.calls == [
        ("stk_week_month_adj", {"freq": "week", "trade_date": "20260727"}, KLINE_FIELDS)
    ]
    assert batch.freq is KlineFreq.WEEK
    assert batch.records[0].trade_date == date(2026, 7, 24)
    assert batch.records[0].end_date is None
    month_client = FakeClient([_kline_rows(0, 1, freq="month")])
    monthly = TushareWeeklyMonthlyKlineProvider(month_client, monotonic=lambda: 0.0).fetch_kline(
        KlineRequest(KlineFreq.MONTH, _TARGET), deadline=10
    )
    assert month_client.calls[0][1]["freq"] == "month"
    assert monthly.records[0].freq is KlineFreq.MONTH


def test_kline_adapter_rejects_period_mismatch_and_isolates_missing_prices() -> None:
    rows = _kline_rows(0, 1, freq="month")
    with pytest.raises(ProviderPayloadError):
        TushareWeeklyMonthlyKlineProvider(FakeClient([rows]), monotonic=lambda: 0.0).fetch_kline(
            KlineRequest(KlineFreq.WEEK, _TARGET), deadline=10
        )
    bad = _kline_rows(0, 2, missing_price=True)
    batch = TushareWeeklyMonthlyKlineProvider(FakeClient([bad]), monotonic=lambda: 0.0).fetch_kline(
        KlineRequest(KlineFreq.WEEK, _TARGET), deadline=10
    )
    assert len(batch.records) == 1
    assert batch.isolated[0].category == "INVALID_FIELD"
    assert batch.isolated[0].field_name == "open"
