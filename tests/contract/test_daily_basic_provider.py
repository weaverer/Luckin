"""DailyBasicProvider 契约测试：Memory 一致性套件与字段泄漏防护。"""

from datetime import date

import pytest

from lucking.ports.daily_basic_provider import (
    DailyBasicProvider,
    DailyBasicRequest,
    ProviderDailyBasic,
)
from tests.contract.market_data_memory import (
    MemoryDailyBasicProvider,
    assert_provider_batch_consistent,
)

_TARGET = date(2026, 7, 27)


def test_memory_provider_exposes_complete_5400_record_contract() -> None:
    provider = MemoryDailyBasicProvider()
    assert isinstance(provider, DailyBasicProvider)
    batch = provider.fetch_daily_basics(DailyBasicRequest(_TARGET), deadline=1.0)
    assert len(batch.records) == batch.evidence.received_count == 5400
    assert_provider_batch_consistent(batch)
    assert all(record.trade_date == _TARGET for record in batch.records)


def test_loss_making_companies_keep_null_valuation_fields() -> None:
    provider = MemoryDailyBasicProvider(loss_making=frozenset({"000001.SH"}))
    batch = provider.fetch_daily_basics(DailyBasicRequest(_TARGET), deadline=1.0)
    loss_making = next(
        record for record in batch.records if record.provider_security_id == "000001.SH"
    )
    assert loss_making.pe is None
    assert loss_making.pb is None
    assert loss_making.turnover_rate is not None
    profitable = next(
        record for record in batch.records if record.provider_security_id == "000002.SH"
    )
    assert profitable.pe is not None


def test_provider_record_field_set_is_exactly_the_contract() -> None:
    fields = set(ProviderDailyBasic.__dataclass_fields__)
    assert fields == {
        "trade_date",
        "provider_security_id",
        "venue_code",
        "security_code",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "limit_status",
    }
    assert "close" not in fields  # 单表事实原则


def test_supplier_field_leakage_is_rejected_by_the_contract() -> None:
    with pytest.raises(TypeError):
        ProviderDailyBasic(  # type: ignore[call-arg]
            trade_date=_TARGET,
            provider_security_id="000001.SH",
            venue_code=None,
            security_code="000001",
            pe=None,
            pe_ttm=None,
            pb=None,
            ps=None,
            ps_ttm=None,
            dv_ratio=None,
            dv_ttm=None,
            total_share=None,
            float_share=None,
            free_share=None,
            total_mv=None,
            circ_mv=None,
            turnover_rate=None,
            turnover_rate_f=None,
            volume_ratio=None,
            limit_status=None,
            close=None,  # 未授权供应商字段
        )
