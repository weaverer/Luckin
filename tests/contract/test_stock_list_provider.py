from dataclasses import fields
from datetime import UTC, date, datetime

import pytest

from lucking.integrations.tushare.client import TushareTable
from lucking.integrations.tushare.stock_list_provider import TushareStockListProvider
from lucking.ports.stock_list_provider import (
    FIXED_VENUES,
    ListingStatus,
    ProviderIncompleteError,
    ProviderStockList,
    ProviderStockRecord,
    RetrievalEvidence,
    ScopeCode,
    StockListProvider,
    StockListRequest,
    VenueCode,
)


class MemoryStockListProvider:
    provider_code = "memory"

    def __init__(self, records: tuple[ProviderStockRecord, ...]) -> None:
        self.records = records

    def fetch_stock_list(
        self, request: StockListRequest, *, deadline: float
    ) -> ProviderStockList:
        assert request.scope_code is ScopeCode.CN_STOCK
        assert deadline > 0
        if not self.records:
            raise ProviderIncompleteError(self.provider_code, "聚合结果为空")
        return ProviderStockList(
            provider_code=self.provider_code,
            scope_code=request.scope_code,
            records=self.records,
            evidence=RetrievalEvidence(12, 12, 0, len(self.records)),
            acquired_at=datetime(2026, 7, 26, tzinfo=UTC),
        )


def sample_records() -> tuple[ProviderStockRecord, ...]:
    return (
        ProviderStockRecord(
            "600000.SH",
            VenueCode.SHANGHAI,
            "600000",
            "浦发银行",
            "CNY",
            ListingStatus.ACTIVE,
            date(1999, 11, 10),
            None,
        ),
        ProviderStockRecord(
            "000001.SZ",
            VenueCode.SHENZHEN,
            "000001",
            "平安银行",
            "CNY",
            ListingStatus.SUSPENDED,
            date(1991, 4, 3),
            None,
        ),
        ProviderStockRecord(
            "430001.BJ",
            VenueCode.BEIJING,
            "430001",
            "测试北交",
            "CNY",
            ListingStatus.PENDING,
            None,
            None,
        ),
    )


def test_provider_contract_is_scope_only_and_runtime_checkable() -> None:
    request = StockListRequest(ScopeCode.CN_STOCK)
    assert [field.name for field in fields(request)] == ["scope_code"]
    assert FIXED_VENUES == (
        VenueCode.SHANGHAI,
        VenueCode.SHENZHEN,
        VenueCode.BEIJING,
    )
    provider = MemoryStockListProvider(sample_records())
    assert isinstance(provider, StockListProvider)
    result = provider.fetch_stock_list(request, deadline=1.0)
    assert result.evidence.completed_segment_count == 12
    assert {record.venue_code for record in result.records} == set(FIXED_VENUES)


def test_canonical_record_contains_only_list_semantics() -> None:
    assert [field.name for field in fields(ProviderStockRecord)] == [
        "provider_security_id",
        "venue_code",
        "security_code",
        "display_name",
        "currency_code",
        "listing_status",
        "listed_on",
        "delisted_on",
    ]


def test_memory_provider_rejects_empty_aggregate() -> None:
    with pytest.raises(ProviderIncompleteError):
        MemoryStockListProvider(()).fetch_stock_list(
            StockListRequest(ScopeCode.CN_STOCK), deadline=1.0
        )


def test_memory_and_tushare_produce_the_same_golden_semantics() -> None:
    canonical = sample_records()[0]

    class Client:
        def call(self, api_name, *, params, fields, allow_empty=False):
            rows = ()
            if params == {"exchange": "SSE", "list_status": "L"}:
                rows = (
                    {
                        "ts_code": canonical.provider_security_id,
                        "symbol": canonical.security_code,
                        "name": canonical.display_name,
                        "exchange": "SSE",
                        "curr_type": canonical.currency_code,
                        "list_status": "L",
                        "list_date": canonical.listed_on.strftime("%Y%m%d"),
                        "delist_date": "",
                    },
                )
            return TushareTable(tuple(fields), rows)

    tushare = TushareStockListProvider(
        Client(),
        now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
        monotonic=lambda: 1.0,
        sleep=lambda _: None,
    ).fetch_stock_list(StockListRequest(), deadline=2.0)
    memory = MemoryStockListProvider((canonical,)).fetch_stock_list(
        StockListRequest(), deadline=2.0
    )
    assert tushare.records == memory.records
