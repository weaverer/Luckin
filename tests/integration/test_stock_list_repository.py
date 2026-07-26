from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from lucking.models.stock_list import (
    StockCurrent,
    StockListSyncIssue,
    StockListSyncRun,
    StockProviderMapping,
    SyncStatus,
)
from lucking.ports.stock_list_provider import (
    ListingStatus,
    RetrievalEvidence,
    VenueCode,
)
from lucking.repositories.stock_list import (
    PublishRecord,
    SqlAlchemyStockListRepository,
)


def test_stock_list_schema_has_four_tables_and_expected_keys(
    sqlite_session_factory,
) -> None:
    engine = sqlite_session_factory.kw["bind"]
    inspector = inspect(engine)
    assert {
        StockCurrent.__tablename__,
        StockProviderMapping.__tablename__,
        StockListSyncRun.__tablename__,
        StockListSyncIssue.__tablename__,
    }.issubset(set(inspector.get_table_names()))

    current_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("stock_current")
    }
    assert ("market_code", "venue_code", "security_code") in current_uniques
    run_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("stock_list_sync_run")
    }
    assert ("run_key",) in run_uniques


def test_stock_current_has_no_provider_or_out_of_scope_columns(
    sqlite_session_factory,
) -> None:
    inspector = inspect(sqlite_session_factory.kw["bind"])
    columns = {column["name"] for column in inspector.get_columns("stock_current")}
    assert "provider_security_id" not in columns
    assert not columns.intersection(
        {"area", "industry", "fullname", "market", "is_hs", "act_name"}
    )


def test_first_claim_publish_and_query_are_atomic(sqlite_session_factory) -> None:
    repository = SqlAlchemyStockListRepository(sqlite_session_factory)
    now = datetime(2026, 7, 26, tzinfo=UTC)
    claim = repository.claim_run(
        run_key="a" * 64,
        schedule_slug="daily-stock-list",
        scheduled_for=now,
        business_date=date(2026, 7, 26),
        scope_fingerprint="b" * 64,
        provider_code="memory",
        flow_run_id="flow-1",
        started_at=now,
        is_manual_retry=False,
    )
    assert claim.status is SyncStatus.RUNNING
    repository.publish_success(
        claim,
        provider_code="memory",
        records=(
            PublishRecord(
                "stock-1",
                "600000.SH",
                VenueCode.SHANGHAI,
                "600000",
                "浦发银行",
                "CNY",
                ListingStatus.ACTIVE,
                date(1999, 11, 10),
                None,
                True,
                True,
            ),
        ),
        evidence=RetrievalEvidence(12, 12, 0, 1),
        duplicate_count=0,
        candidate_digest="c" * 64,
        completed_at=now,
    )
    rows = repository.list_current(venue_code=VenueCode.SHANGHAI)
    assert len(rows) == 1
    assert rows[0].security_code == "600000"
    assert not hasattr(rows[0], "provider_security_id")


def test_run_key_short_circuit_and_explicit_retry(sqlite_session_factory) -> None:
    repository = SqlAlchemyStockListRepository(sqlite_session_factory)
    now = datetime(2026, 7, 26, tzinfo=UTC)
    kwargs = {
        "run_key": "d" * 64,
        "schedule_slug": "daily-stock-list",
        "scheduled_for": now,
        "business_date": date(2026, 7, 26),
        "scope_fingerprint": "e" * 64,
        "provider_code": "memory",
        "flow_run_id": "flow-1",
        "started_at": now,
        "is_manual_retry": False,
    }
    first = repository.claim_run(**kwargs)
    same = repository.claim_run(**kwargs)
    assert same.run_id == first.run_id
    assert same.should_execute is False
    repository.record_failure(
        first,
        category="TEST",
        summary="failure",
        completed_at=now,
    )
    blocked = repository.claim_run(**{**kwargs, "flow_run_id": "flow-2"})
    assert blocked.status is SyncStatus.FAILED
    retry = repository.claim_run(
        **{**kwargs, "flow_run_id": "flow-3", "is_manual_retry": True}
    )
    assert retry.run_id == first.run_id
    assert retry.attempt_count == 2
    assert retry.should_execute is True


def test_publish_rolls_back_all_rows_on_identity_constraint_failure(
    sqlite_session_factory,
) -> None:
    repository = SqlAlchemyStockListRepository(sqlite_session_factory)
    now = datetime(2026, 7, 26, tzinfo=UTC)
    claim = repository.claim_run(
        run_key="f" * 64,
        schedule_slug="daily-stock-list",
        scheduled_for=now,
        business_date=date(2026, 7, 26),
        scope_fingerprint="1" * 64,
        provider_code="memory",
        flow_run_id="flow",
        started_at=now,
        is_manual_retry=False,
    )
    duplicate_identity = tuple(
        PublishRecord(
            f"stock-{index}",
            f"provider-{index}",
            VenueCode.SHANGHAI,
            "600001",
            f"股票{index}",
            "CNY",
            ListingStatus.ACTIVE,
            date(2000, 1, 1),
            None,
            True,
            True,
        )
        for index in range(2)
    )
    with pytest.raises(IntegrityError):
        repository.publish_success(
            claim,
            provider_code="memory",
            records=duplicate_identity,
            evidence=RetrievalEvidence(12, 12, 0, 2),
            duplicate_count=0,
            candidate_digest="2" * 64,
            completed_at=now,
        )
    with sqlite_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(StockCurrent)) == 0


@pytest.mark.mysql
def test_mysql_publish_is_atomic_on_identity_constraint_failure(
    mysql_session_factory,
) -> None:
    repository = SqlAlchemyStockListRepository(mysql_session_factory)
    now = datetime(2026, 7, 26, tzinfo=UTC)
    token = uuid4().hex
    claim = repository.claim_run(
        run_key=token * 2,
        schedule_slug="mysql-atomic-verification",
        scheduled_for=now,
        business_date=now.date(),
        scope_fingerprint="a" * 64,
        provider_code="memory",
        flow_run_id=f"flow-{token}",
        started_at=now,
        is_manual_retry=False,
    )
    with mysql_session_factory() as session:
        count_before = session.scalar(select(func.count()).select_from(StockCurrent))
    duplicate_identity = tuple(
        PublishRecord(
            str(uuid4()),
            f"{token}-{index}",
            VenueCode.SHANGHAI,
            token[:12],
            f"MySQL 原子性验证 {index}",
            "CNY",
            ListingStatus.ACTIVE,
            date(2000, 1, 1),
            None,
            True,
            True,
        )
        for index in range(2)
    )
    try:
        with pytest.raises(IntegrityError):
            repository.publish_success(
                claim,
                provider_code="memory",
                records=duplicate_identity,
                evidence=RetrievalEvidence(12, 12, 0, 2),
                duplicate_count=0,
                candidate_digest="b" * 64,
                completed_at=now,
            )
        with mysql_session_factory() as session:
            assert (
                session.scalar(select(func.count()).select_from(StockCurrent))
                == count_before
            )
            run = session.get(StockListSyncRun, claim.run_id)
            assert run is not None
            assert run.status == SyncStatus.RUNNING.value
    finally:
        with mysql_session_factory.begin() as session:
            session.query(StockListSyncRun).filter(
                StockListSyncRun.run_id == claim.run_id
            ).delete()


def test_expired_running_claim_requires_explicit_retry_and_records_abandoned(
    sqlite_session_factory,
) -> None:
    repository = SqlAlchemyStockListRepository(
        sqlite_session_factory, running_lease_seconds=60
    )
    started = datetime(2026, 7, 26, tzinfo=UTC)
    kwargs = {
        "run_key": "9" * 64,
        "schedule_slug": "daily-stock-list",
        "scheduled_for": started,
        "business_date": started.date(),
        "scope_fingerprint": "8" * 64,
        "provider_code": "memory",
        "flow_run_id": "flow-1",
        "started_at": started,
        "is_manual_retry": False,
    }
    first = repository.claim_run(**kwargs)
    blocked = repository.claim_run(
        **{
            **kwargs,
            "flow_run_id": "flow-2",
            "started_at": started + timedelta(seconds=61),
        }
    )
    assert blocked.should_execute is False
    retry = repository.claim_run(
        **{
            **kwargs,
            "flow_run_id": "flow-3",
            "started_at": started + timedelta(seconds=61),
            "is_manual_retry": True,
        }
    )
    assert retry.run_id == first.run_id
    with sqlite_session_factory() as session:
        issue = session.scalar(
            select(StockListSyncIssue).where(
                StockListSyncIssue.run_id == first.run_id,
                StockListSyncIssue.category == "ABANDONED",
            )
        )
        run = session.get(StockListSyncRun, first.run_id)
        assert issue is not None
        assert run.attempt_count == 2
