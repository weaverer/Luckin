"""AdjFactorProvider 契约测试：Memory 一致性套件与字段泄漏防护。"""

from datetime import date
from decimal import Decimal

import pytest

from lucking.models.market_data import VenueCode
from lucking.ports.adj_factor_provider import (
    AdjFactorProvider,
    AdjFactorRequest,
    ProviderAdjFactor,
)
from tests.contract.market_data_memory import (
    MemoryAdjFactorProvider,
    assert_provider_batch_consistent,
)

_TARGET = date(2026, 7, 27)


def test_memory_provider_exposes_complete_5400_record_contract() -> None:
    provider = MemoryAdjFactorProvider()
    assert isinstance(provider, AdjFactorProvider)
    batch = provider.fetch_adj_factors(AdjFactorRequest(_TARGET), deadline=1.0)
    assert len(batch.records) == batch.evidence.received_count == 5400
    assert_provider_batch_consistent(batch)
    assert all(record.trade_date == _TARGET for record in batch.records)
    assert all(record.adj_factor > 0 for record in batch.records)
    first = batch.records[0]
    assert first.venue_code is VenueCode.SHANGHAI
    assert first.security_code == "000001"
    assert isinstance(first.adj_factor, Decimal)


def test_provider_record_field_set_is_exactly_the_contract() -> None:
    assert set(ProviderAdjFactor.__dataclass_fields__) == {
        "trade_date",
        "provider_security_id",
        "venue_code",
        "security_code",
        "adj_factor",
    }


def test_supplier_field_leakage_is_rejected_by_the_contract() -> None:
    with pytest.raises(TypeError):
        ProviderAdjFactor(  # type: ignore[call-arg]
            trade_date=_TARGET,
            provider_security_id="000001.SH",
            venue_code=None,
            security_code="000001",
            adj_factor=None,
            extra_field=None,  # 未授权供应商字段
        )
