from datetime import UTC, date, datetime
from time import perf_counter

from lucking.ports.stock_list_provider import ListingStatus, RetrievalEvidence, VenueCode
from lucking.repositories.stock_list import PublishRecord, SqlAlchemyStockListRepository


def _records(count: int) -> tuple[PublishRecord, ...]:
    venues = tuple(VenueCode)
    return tuple(
        PublishRecord(
            f"stock-{index:05d}",
            f"provider-{index:05d}",
            venues[index % len(venues)],
            f"{index:06d}",
            f"测试股票{index:05d}",
            "CNY",
            ListingStatus.ACTIVE,
            date(2020, 1, 1),
            None,
            True,
            True,
        )
        for index in range(count)
    )


def test_ten_thousand_record_publish_and_current_query_p95(
    sqlite_session_factory,
) -> None:
    repository = SqlAlchemyStockListRepository(sqlite_session_factory)
    now = datetime(2026, 7, 26, tzinfo=UTC)
    claim = repository.claim_run(
        run_key="7" * 64,
        schedule_slug="daily-stock-list",
        scheduled_for=now,
        business_date=now.date(),
        scope_fingerprint="6" * 64,
        provider_code="memory",
        flow_run_id="flow-performance",
        started_at=now,
        is_manual_retry=False,
    )
    records = _records(10_000)
    repository.publish_success(
        claim,
        provider_code="memory",
        records=records,
        evidence=RetrievalEvidence(12, 12, 0, 10_000),
        duplicate_count=0,
        candidate_digest="5" * 64,
        completed_at=now,
    )
    assert len(repository.list_current(limit=1000)) == 1000

    repository.list_current(security_code="000001", limit=10)
    elapsed = []
    for index in range(100):
        started = perf_counter()
        selector = index % 5
        if selector == 0:
            repository.list_current(limit=1000)
        elif selector == 1:
            repository.list_current(security_code=f"{index:06d}", limit=10)
        elif selector == 2:
            repository.list_current(venue_code=VenueCode.SHANGHAI, limit=1000)
        elif selector == 3:
            repository.list_current(name_query="测试股票", limit=1000)
        else:
            repository.list_current(listing_status=ListingStatus.ACTIVE, limit=1000)
        elapsed.append(perf_counter() - started)
    assert sum(duration <= 1.0 for duration in elapsed) >= 95

