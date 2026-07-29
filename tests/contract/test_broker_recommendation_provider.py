from datetime import UTC, date, datetime

from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationProvider,
    BrokerRecommendationRequest,
    ProviderBrokerRecommendation,
    ProviderBrokerRecommendationBatch,
    RetrievalEvidence,
    VenueCode,
)


class MemoryProvider:
    provider_code = "memory"

    def fetch_month(
        self, request: BrokerRecommendationRequest, *, deadline: float
    ) -> ProviderBrokerRecommendationBatch:
        records = tuple(
            ProviderBrokerRecommendation(
                request.target_month,
                f"券商 {index % 5}",
                f"{index:06d}.SH",
                VenueCode.SHANGHAI,
                f"{index:06d}",
                f"股票 {index}",
            )
            for index in range(2500)
        )
        return ProviderBrokerRecommendationBatch(
            self.provider_code,
            request.target_month,
            records,
            RetrievalEvidence(3, 3, 0, 3, 1000, 500, 2500, True, True),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


def test_memory_provider_exposes_complete_2500_record_contract() -> None:
    provider = MemoryProvider()
    assert isinstance(provider, BrokerRecommendationProvider)
    batch = provider.fetch_month(BrokerRecommendationRequest(date(2026, 7, 1)), deadline=1.0)
    assert len(batch.records) == batch.evidence.received_count == 2500
    assert batch.evidence.last_page_count < batch.evidence.page_limit
    assert batch.evidence.continuation_exhausted
    assert set(batch.records[0].__dataclass_fields__) == {
        "recommendation_month",
        "broker_name",
        "provider_security_id",
        "venue_code",
        "security_code",
        "stock_name",
    }
