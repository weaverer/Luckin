from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.db import Base
from lucking.models.broker_recommendation import (
    BrokerRecommendation,
    BrokerRecommendationSyncAttempt,
    BrokerRecommendationSyncIssue,
    BrokerRecommendationSyncRun,
)
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.ports.broker_recommendation_provider import VenueCode
from lucking.repositories.broker_recommendation import (
    BrokerRecommendationQuery,
    IdentityCandidate,
    RecommendationWrite,
    SqlAlchemyBrokerRecommendationRepository,
    SyncCounts,
    SyncIssue,
)


def _repository(
    tmp_path: Path,
) -> tuple[SqlAlchemyBrokerRecommendationRepository, sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'broker.sqlite'}")

    @event.listens_for(engine, "connect")
    def _register_collation(connection: object, _record: object) -> None:
        connection.create_collation(  # type: ignore[attr-defined]
            "utf8mb4_bin", lambda left, right: (left > right) - (left < right)
        )

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        now = datetime.now(UTC).replace(tzinfo=None)
        session.add(
            StockCurrent(
                stock_id="stock-1",
                market_code="CN-S",
                venue_code="XSHG",
                security_code="600000",
                display_name="浦发银行",
                currency_code="CNY",
                listing_status="ACTIVE",
                listed_on=date(1999, 11, 10),
                delisted_on=None,
                last_seen_run_id="stock-run",
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            StockProviderMapping(
                provider_code="memory",
                provider_security_id="600000.SH",
                stock_id="stock-1",
                last_seen_run_id="stock-run",
                last_seen_at=now,
                created_at=now,
            )
        )
    return SqlAlchemyBrokerRecommendationRepository(factory), factory


def _claim(
    repository: SqlAlchemyBrokerRecommendationRepository,
    run_key: str,
    flow_run_id: str,
) -> object:
    return repository.claim_run_and_start_attempt(
        run_key=run_key,
        run_kind="SCHEDULED",
        target_month=date(2026, 7, 1),
        scope_fingerprint="scope",
        flow_run_id=flow_run_id,
        provider_code="memory",
        page_limit=1000,
        schedule_slug="monthly",
        scheduled_for=datetime(2026, 7, 3, 4, 0),
    )


def test_identity_publish_refresh_and_missing_rows_are_not_deleted(tmp_path: Path) -> None:
    repository, factory = _repository(tmp_path)
    identity = repository.resolve_stock_identity(
        IdentityCandidate("memory", "600000.SH", VenueCode.SHANGHAI, "600000")
    )
    assert identity is not None and identity.stock_id == "stock-1"
    first = _claim(repository, "run-key-3", "flow-3")
    record = RecommendationWrite(
        date(2026, 7, 1),
        "券商 A",
        "stock-1",
        VenueCode.SHANGHAI,
        "600000",
        "浦发银行",
    )
    counts = repository.publish_success(
        first,
        (record,),
        SyncCounts(received_count=2, valid_count=1, invalid_count=1),
        (
            SyncIssue(
                "UNKNOWN_STOCK_IDENTITY",
                "该推荐无法解析到既有股票身份，已跳过",
                provider_security_id_hash="a" * 64,
            ),
        ),
    )
    assert counts.added_count == 1

    second = repository.claim_run_and_start_attempt(
        run_key="run-key-4",
        run_kind="SCHEDULED",
        target_month=date(2026, 7, 1),
        scope_fingerprint="scope",
        flow_run_id="flow-4",
        provider_code="memory",
        page_limit=1000,
        schedule_slug="monthly",
        scheduled_for=datetime(2026, 7, 4, 4, 0),
    )
    changed = RecommendationWrite(
        date(2026, 7, 1),
        "券商 A",
        "stock-1",
        VenueCode.SHANGHAI,
        "600000",
        "浦发",
    )
    counts = repository.publish_success(
        second, (changed,), SyncCounts(received_count=1, valid_count=1)
    )
    assert counts.updated_count == 1
    items = repository.list_month(BrokerRecommendationQuery(date(2026, 7, 1), broker_name="券商 A"))
    assert len(items) == 1 and items[0].stock_name == "浦发"
    with factory() as session:
        row = session.scalar(select(BrokerRecommendation))
        assert row is not None
        assert row.first_seen_run_id == first.run_id
        assert row.last_confirmed_run_id == second.run_id
        issues = session.scalars(select(BrokerRecommendationSyncIssue)).all()
        assert len(issues) == 1
        assert issues[0].category == "UNKNOWN_STOCK_IDENTITY"


def test_failed_or_expired_run_retries_original_run(tmp_path: Path) -> None:
    repository, factory = _repository(tmp_path)
    claim = repository.claim_run_and_start_attempt(
        run_key="backfill-key",
        run_kind="BACKFILL",
        target_month=date(2026, 6, 1),
        scope_fingerprint="scope",
        flow_run_id="backfill-1",
        provider_code="memory",
        page_limit=1000,
        backfill_batch_id="batch",
    )
    repository.record_failure(
        claim,
        SyncCounts(),
        category="PROVIDER_UNAVAILABLE",
        summary="上游暂时不可用",
    )
    resolution = repository.resolve_backfill_month(
        backfill_batch_id="batch", target_month=date(2026, 6, 1)
    )
    assert resolution.run_id == claim.run_id
    assert resolution.action.value == "RETRY"
    retry = repository.claim_run_and_start_attempt(
        run_key="",
        run_kind="",
        target_month=date.min,
        scope_fingerprint="",
        flow_run_id="backfill-2",
        provider_code="memory",
        page_limit=1000,
        retry_run_id=claim.run_id,
    )
    assert retry.run_id == claim.run_id and retry.attempt_no == 2

    with factory.begin() as session:
        attempt = session.scalar(
            select(BrokerRecommendationSyncAttempt).where(
                BrokerRecommendationSyncAttempt.attempt_id == retry.attempt_id
            )
        )
        assert attempt is not None
        attempt.lease_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    expired = repository.resolve_backfill_month(
        backfill_batch_id="batch", target_month=date(2026, 6, 1)
    )
    assert expired.action.value == "RETRY"
    with factory() as session:
        assert session.scalar(select(BrokerRecommendationSyncRun)) is not None
