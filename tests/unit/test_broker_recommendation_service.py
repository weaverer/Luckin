from dataclasses import replace
from datetime import UTC, date, datetime

from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationRequest,
    ProviderBrokerRecommendation,
    ProviderBrokerRecommendationBatch,
    RetrievalEvidence,
    VenueCode,
)
from lucking.repositories.broker_recommendation import (
    AttemptClaim,
    BrokerRecommendationQuery,
    IdentityCandidate,
    RecommendationWrite,
    ResolvedStockIdentity,
    SyncCounts,
    SyncIssue,
)
from lucking.services.broker_recommendation import (
    BackfillBrokerRecommendationMonthCommand,
    BrokerRecommendationService,
    backfill_run_key,
    scheduled_run_key,
)


def test_scheduled_run_key_uses_utc_schedule_and_target_month_only() -> None:
    target = date(2026, 7, 1)
    instant = datetime(2026, 7, 3, 4, 0, tzinfo=UTC)
    assert scheduled_run_key("monthly", instant, target) == scheduled_run_key(
        "monthly", datetime(2026, 7, 3, 12, 0).astimezone(), target
    )
    assert scheduled_run_key("monthly", instant, target) != scheduled_run_key(
        "monthly", datetime(2026, 7, 4, 4, 0, tzinfo=UTC), target
    )


def test_backfill_run_key_ignores_provider_and_config_by_signature() -> None:
    target = date(2026, 7, 1)
    assert backfill_run_key("initialization", target) == backfill_run_key("initialization", target)
    assert backfill_run_key("batch-a", target) != backfill_run_key("batch-b", target)


def test_one_unknown_stock_is_skipped_without_failing_valid_month() -> None:
    target = date(2026, 7, 1)

    class Provider:
        provider_code = "memory"

        def fetch_month(
            self, request: BrokerRecommendationRequest, *, deadline: float
        ) -> ProviderBrokerRecommendationBatch:
            records = (
                ProviderBrokerRecommendation(
                    target,
                    "券商 A",
                    "000001.SZ",
                    VenueCode.SHENZHEN,
                    "000001",
                    "未知股票",
                ),
                ProviderBrokerRecommendation(
                    target,
                    "券商 A",
                    "600000.SH",
                    VenueCode.SHANGHAI,
                    "600000",
                    "浦发银行",
                ),
            )
            return ProviderBrokerRecommendationBatch(
                "memory",
                target,
                records,
                RetrievalEvidence(1, 1, 0, 1, 1000, 2, 2, False, True),
                datetime(2026, 7, 1, tzinfo=UTC),
            )

    class Repository:
        published: (
            tuple[
                tuple[RecommendationWrite, ...],
                SyncCounts,
                tuple[SyncIssue, ...],
            ]
            | None
        ) = None

        def claim_run_and_start_attempt(self, **kwargs: object) -> AttemptClaim:
            return AttemptClaim(
                "run-id",
                str(kwargs["run_key"]),
                "attempt-id",
                1,
                target,
                "BACKFILL",
                "initial",
            )

        def resolve_stock_identity(
            self, candidate: IdentityCandidate
        ) -> ResolvedStockIdentity | None:
            if candidate.security_code == "000001":
                return None
            return ResolvedStockIdentity("stock-id", VenueCode.SHANGHAI, "600000")

        def publish_success(
            self,
            claim: AttemptClaim,
            records: tuple[RecommendationWrite, ...],
            counts: SyncCounts,
            issues: tuple[SyncIssue, ...] = (),
        ) -> SyncCounts:
            self.published = records, counts, issues
            return replace(counts, added_count=len(records))

        def record_failure(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("包含有效股票的月份不应失败")

        def resolve_backfill_month(self, **kwargs: object) -> object:
            raise NotImplementedError

        def list_month(self, query: BrokerRecommendationQuery) -> tuple[object, ...]:
            return ()

    repository = Repository()
    result = BrokerRecommendationService(Provider(), repository, monotonic=lambda: 0.0).sync(
        BackfillBrokerRecommendationMonthCommand(target, "initial", "flow-run-id")
    )

    assert result.status.value == "SUCCEEDED"
    assert result.received_count == 2
    assert result.valid_count == 1
    assert result.invalid_count == 1
    assert result.added_count == 1
    assert repository.published is not None
    records, counts, issues = repository.published
    assert len(records) == 1 and records[0].security_code == "600000"
    assert counts.invalid_count == 1
    assert len(issues) == 1
    assert issues[0].category == "UNKNOWN_STOCK_IDENTITY"
    assert issues[0].provider_security_id_hash is not None
