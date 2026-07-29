from datetime import UTC, date, datetime

from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationRequest,
    ProviderBrokerRecommendation,
    ProviderBrokerRecommendationBatch,
    RetrievalEvidence,
    VenueCode,
)


def test_memory_batch_represents_1000_1000_500_complete_pages() -> None:
    target = date(2026, 7, 1)
    records = tuple(
        ProviderBrokerRecommendation(
            target,
            f"券商{index % 20}",
            f"{index:06d}.SH",
            VenueCode.SHANGHAI,
            f"{index:06d}",
            f"股票{index}",
        )
        for index in range(2500)
    )
    batch = ProviderBrokerRecommendationBatch(
        "memory",
        BrokerRecommendationRequest(target).target_month,
        records,
        RetrievalEvidence(3, 3, 0, 3, 1000, 500, 2500, True, True),
        datetime(2026, 7, 4, tzinfo=UTC),
    )
    assert len(batch.records) == 2500
    assert batch.evidence.page_count == 3
    assert batch.evidence.continuation_exhausted
