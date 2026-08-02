"""WeeklyMonthlyKlineProvider 契约测试：freq 分派、三组价格、周期归属。"""

from datetime import date

import pytest

from lucking.ports.weekly_monthly_kline_provider import (
    KlineFreq,
    KlineRequest,
    ProviderWeeklyMonthlyKline,
    WeeklyMonthlyKlineProvider,
)
from tests.contract.market_data_memory import (
    MemoryWeeklyMonthlyKlineProvider,
    assert_provider_batch_consistent,
)

_TARGET = date(2026, 7, 27)


def test_memory_provider_dispatches_week_and_month_independently() -> None:
    provider = MemoryWeeklyMonthlyKlineProvider()
    assert isinstance(provider, WeeklyMonthlyKlineProvider)
    weekly = provider.fetch_kline(KlineRequest(KlineFreq.WEEK, _TARGET), deadline=1.0)
    monthly = provider.fetch_kline(KlineRequest(KlineFreq.MONTH, _TARGET), deadline=1.0)
    assert len(weekly.records) == weekly.evidence.received_count == 5400
    assert len(monthly.records) == 5400
    assert_provider_batch_consistent(weekly)
    assert all(record.freq is KlineFreq.WEEK for record in weekly.records)
    assert all(record.freq is KlineFreq.MONTH for record in monthly.records)
    # 2026-07-27 是周一：周线周期最后交易日为 2026-07-24（周五）
    assert all(record.trade_date == date(2026, 7, 24) for record in weekly.records)
    # 月线周期最后交易日为 2026-06-30（上一月末）
    assert all(record.trade_date == date(2026, 6, 30) for record in monthly.records)


def test_unadjusted_prices_are_all_present() -> None:
    provider = MemoryWeeklyMonthlyKlineProvider()
    batch = provider.fetch_kline(KlineRequest(KlineFreq.WEEK, _TARGET), deadline=1.0)
    first = batch.records[0]
    assert first.open is not None and first.close is not None
    assert first.end_date is None  # 与 trade_date 一致时为空


def test_same_period_multi_day_requests_return_same_trade_date() -> None:
    provider = MemoryWeeklyMonthlyKlineProvider()
    monday = provider.fetch_kline(KlineRequest(KlineFreq.WEEK, date(2026, 7, 20)), deadline=1.0)
    tuesday = provider.fetch_kline(KlineRequest(KlineFreq.WEEK, date(2026, 7, 21)), deadline=1.0)
    # 同一自然周内多日请求返回相同周期最后交易日（2026-07-17 周五）
    assert all(record.trade_date == date(2026, 7, 17) for record in monday.records)
    assert all(record.trade_date == date(2026, 7, 17) for record in tuesday.records)


def test_provider_record_field_set_is_exactly_the_contract() -> None:
    assert set(ProviderWeeklyMonthlyKline.__dataclass_fields__) == {
        "freq",
        "trade_date",
        "end_date",
        "provider_security_id",
        "venue_code",
        "security_code",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
        "change",
        "pct_chg",
    }


def test_supplier_field_leakage_is_rejected_by_the_contract() -> None:
    with pytest.raises(TypeError):
        ProviderWeeklyMonthlyKline(  # type: ignore[call-arg]
            freq=KlineFreq.WEEK,
            trade_date=_TARGET,
            end_date=None,
            provider_security_id="000001.SH",
            venue_code=None,
            security_code="000001",
            open=None,
            high=None,
            low=None,
            close=None,
            vol=None,
            amount=None,
            change=None,
            pct_chg=None,
            qfq_close=None,  # 未授权供应商字段（实测接口无复权价）
        )
