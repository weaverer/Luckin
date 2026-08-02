"""DailyQuoteProvider 契约测试：Memory 一致性套件与字段泄漏防护。"""

from datetime import date

import pytest

from lucking.ports.daily_quote_provider import (
    DailyQuoteProvider,
    DailyQuoteRequest,
    ProviderDailyQuote,
)
from tests.contract.market_data_memory import (
    MemoryDailyQuoteProvider,
    assert_provider_batch_consistent,
)

_TARGET = date(2026, 7, 27)


def test_memory_provider_exposes_complete_5400_record_contract() -> None:
    provider = MemoryDailyQuoteProvider()
    assert isinstance(provider, DailyQuoteProvider)
    batch = provider.fetch_daily_quotes(DailyQuoteRequest(_TARGET), deadline=1.0)
    assert len(batch.records) == batch.evidence.received_count == 5400
    assert_provider_batch_consistent(batch)
    assert batch.target_trade_date == _TARGET
    assert all(record.trade_date == _TARGET for record in batch.records)
    assert {record.venue_code for record in batch.records} == {
        record.venue_code for record in batch.records
    }


def test_memory_provider_omits_suspended_stocks_without_failing() -> None:
    provider = MemoryDailyQuoteProvider(suspended=frozenset({"000001.SH", "000002.SH"}))
    batch = provider.fetch_daily_quotes(DailyQuoteRequest(_TARGET), deadline=1.0)
    assert len(batch.records) == 5398
    assert all(
        record.provider_security_id not in {"000001.SH", "000002.SH"}
        for record in batch.records
    )
    assert_provider_batch_consistent(batch)


def test_provider_record_field_set_is_exactly_the_contract() -> None:
    assert set(ProviderDailyQuote.__dataclass_fields__) == {
        "trade_date",
        "provider_security_id",
        "venue_code",
        "security_code",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    }


def test_supplier_field_leakage_is_rejected_by_the_contract() -> None:
    """供应商新增字段（如盘后成交量）不得进入规范 DTO。"""
    with pytest.raises(TypeError):
        ProviderDailyQuote(  # type: ignore[call-arg]
            trade_date=_TARGET,
            provider_security_id="000001.SH",
            venue_code=None,
            security_code="000001",
            open=None,
            high=None,
            low=None,
            close=None,
            pre_close=None,
            change=None,
            pct_chg=None,
            vol=None,
            amount=None,
            ah_vol=None,  # 未授权供应商字段
        )
