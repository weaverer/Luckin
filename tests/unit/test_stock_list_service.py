from datetime import UTC, date, datetime

import pytest

from lucking.models.stock_list import SyncStatus
from lucking.ports.stock_list_provider import (
    ListingStatus,
    ProviderStockList,
    ProviderStockRecord,
    RetrievalEvidence,
    ScopeCode,
    VenueCode,
)
from lucking.repositories.stock_list import RunClaim, StockListItem
from lucking.services.stock_list import (
    InvalidStockList,
    StockListService,
    StockListSyncCommand,
)


class Provider:
    provider_code = "memory"

    def __init__(self, records) -> None:
        self.records = records
        self.deadline = None
        self.calls = 0

    def fetch_stock_list(self, request, *, deadline):
        self.calls += 1
        self.deadline = deadline
        return ProviderStockList(
            "memory",
            request.scope_code,
            tuple(self.records),
            RetrievalEvidence(12, 12, 0, len(self.records)),
            datetime(2026, 7, 26, tzinfo=UTC),
        )


class Repository:
    def __init__(self) -> None:
        self.published = None
        self.claim = RunClaim("run-1", "key", SyncStatus.RUNNING, 1, True)

    def claim_run(self, **kwargs):
        self.claim_kwargs = kwargs
        return self.claim

    def provider_mappings(self, provider_code):
        return {}

    def resolve_records(self, provider_code, records):
        from lucking.repositories.stock_list import PublishRecord

        return tuple(
            PublishRecord(
                f"id-{index}",
                row.provider_security_id,
                row.venue_code,
                row.security_code,
                row.display_name,
                row.currency_code,
                row.listing_status,
                row.listed_on,
                row.delisted_on,
                True,
                True,
            )
            for index, row in enumerate(records)
        )

    def publish_success(self, claim, **kwargs):
        self.published = kwargs

    def record_failure(self, claim, **kwargs):
        self.failed = kwargs

    def list_current(self, **kwargs):
        return [
            StockListItem(
                "id-1",
                "CN-S",
                VenueCode.SHANGHAI,
                "600000",
                "浦发银行",
                "CNY",
                ListingStatus.ACTIVE,
                date(1999, 11, 10),
                None,
            )
        ]


def _record() -> ProviderStockRecord:
    return ProviderStockRecord(
        "600000.SH",
        VenueCode.SHANGHAI,
        "600000",
        "浦发银行",
        "CNY",
        ListingStatus.ACTIVE,
        date(1999, 11, 10),
        None,
    )


def test_first_sync_uses_beijing_business_date_and_25_minute_deadline() -> None:
    provider, repository = Provider([_record()]), Repository()
    service = StockListService(
        provider,
        repository,
        now=lambda: datetime(2026, 7, 26, 1, 5, tzinfo=UTC),
        monotonic=lambda: 100.0,
        fetch_deadline_seconds=1500,
    )
    result = service.sync(
        StockListSyncCommand(
            "daily-stock-list",
            datetime(2026, 7, 26, 1, 0, tzinfo=UTC),
            ScopeCode.CN_STOCK,
            "flow-1",
        )
    )
    assert result.status is SyncStatus.SUCCEEDED
    assert result.business_date == date(2026, 7, 26)
    assert provider.deadline == 1600.0
    assert repository.published is not None


def test_current_query_validates_pagination_before_repository() -> None:
    service = StockListService(Provider([_record()]), Repository())
    assert service.list_current(venue_code=VenueCode.SHANGHAI)[0].stock_id == "id-1"
    with pytest.raises(InvalidStockList):
        service.list_current(limit=0)
    with pytest.raises(InvalidStockList):
        service.list_current(offset=-1)


@pytest.mark.parametrize(
    "evidence",
    [
        RetrievalEvidence(12, 11, 0, 1),
        RetrievalEvidence(12, 12, 1, 1),
        RetrievalEvidence(11, 11, 0, 1),
    ],
)
def test_incomplete_coverage_never_publishes(evidence) -> None:
    provider, repository = Provider([_record()]), Repository()

    def incomplete(request, *, deadline):
        return ProviderStockList(
            "memory",
            request.scope_code,
            (_record(),),
            evidence,
            datetime(2026, 7, 26, tzinfo=UTC),
        )

    provider.fetch_stock_list = incomplete
    with pytest.raises(InvalidStockList, match="覆盖证明"):
        StockListService(provider, repository).sync(
            StockListSyncCommand(
                "daily-stock-list",
                datetime(2026, 7, 26, tzinfo=UTC),
                ScopeCode.CN_STOCK,
                "flow-1",
            )
        )
    assert repository.published is None

