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
from lucking.repositories.stock_list import RunClaim
from lucking.services.stock_list import (
    InvalidStockList,
    StockListService,
    StockListSyncCommand,
)


def _record(
    *,
    provider_id: str = "600000.SH",
    code: str = "600000",
    name: str = "浦发银行",
) -> ProviderStockRecord:
    return ProviderStockRecord(
        provider_id,
        VenueCode.SHANGHAI,
        code,
        name,
        "CNY",
        ListingStatus.ACTIVE,
        date(1999, 11, 10),
        None,
    )


class Provider:
    provider_code = "memory"

    def __init__(self, records) -> None:
        self.records = tuple(records)

    def fetch_stock_list(self, request, *, deadline):
        return ProviderStockList(
            "memory",
            ScopeCode.CN_STOCK,
            self.records,
            RetrievalEvidence(12, 12, 0, len(self.records)),
            datetime(2026, 7, 26, tzinfo=UTC),
        )


class Repository:
    def __init__(self, baseline=()) -> None:
        self.baseline = {key: f"id-{index}" for index, key in enumerate(baseline)}
        self.failed = None
        self.published = None

    def claim_run(self, **kwargs):
        return RunClaim("run-1", "key", SyncStatus.RUNNING, 1, True)

    def provider_mappings(self, provider_code):
        return self.baseline

    def resolve_records(self, provider_code, records):
        from lucking.repositories.stock_list import PublishRecord

        return tuple(
            PublishRecord(
                self.baseline.get(row.provider_security_id, f"new-{index}"),
                row.provider_security_id,
                row.venue_code,
                row.security_code,
                row.display_name,
                row.currency_code,
                row.listing_status,
                row.listed_on,
                row.delisted_on,
                row.provider_security_id not in self.baseline,
                True,
            )
            for index, row in enumerate(records)
        )

    def publish_success(self, claim, **kwargs):
        self.published = kwargs

    def record_failure(self, claim, **kwargs):
        self.failed = kwargs

    def list_current(self, **kwargs):
        return []


def _sync(records, repository=None):
    repository = repository or Repository()
    result = StockListService(
        Provider(records),
        repository,
        now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
        monotonic=lambda: 1.0,
    ).sync(
        StockListSyncCommand(
            "daily-stock-list",
            datetime(2026, 7, 26, tzinfo=UTC),
            ScopeCode.CN_STOCK,
            "flow-1",
        )
    )
    return result, repository


def test_exact_duplicate_is_deduplicated_and_counted() -> None:
    result, repository = _sync([_record(), _record()])
    assert result.valid_count == 1
    assert result.duplicate_count == 1
    assert len(repository.published["records"]) == 1


def test_provider_or_canonical_identity_conflict_fails_whole_batch() -> None:
    with pytest.raises(InvalidStockList, match="IDENTITY_CONFLICT"):
        _sync([_record(), _record(name="冲突名称")])
    with pytest.raises(InvalidStockList, match="IDENTITY_CONFLICT"):
        _sync([_record(), _record(provider_id="other.SH")])


def test_any_missing_baseline_identity_fails_without_publish() -> None:
    repository = Repository(("600000.SH", "000001.SZ"))
    with pytest.raises(InvalidStockList, match="BASELINE_MISSING"):
        _sync([_record()], repository)
    assert repository.published is None
    assert repository.failed["category"] == "INVALID_STOCK_LIST"

