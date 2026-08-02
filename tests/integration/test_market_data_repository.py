"""MySQL 审计 Repository 重试与租约边界测试（sqlite）。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    DataKind,
    MarketDataSyncAttempt,
    MarketDataSyncIssue,
    MarketDataSyncRun,
)
from lucking.repositories.market_data import (
    AttemptClaim,
    SqlAlchemyMarketDataRepository,
    SyncCounts,
)

_TARGET = date(2026, 7, 24)


@pytest.fixture
def repository(sqlite_session_factory: sessionmaker[Session]) -> SqlAlchemyMarketDataRepository:
    return SqlAlchemyMarketDataRepository(sqlite_session_factory)


def _claim(
    repository: SqlAlchemyMarketDataRepository,
    *,
    marker: str,
    index: int = 0,
    retry_run_id: str | None = None,
    run_key: str | None = None,
) -> AttemptClaim:
    return repository.claim_run_and_start_attempt(
        run_key=run_key if run_key is not None else f"{marker}-{index}",
        run_kind="" if retry_run_id is not None else "BACKFILL",
        data_kind=DataKind.DAILY_QUOTE.value,
        target_trade_date=date.min if retry_run_id is not None else _TARGET,
        scope_fingerprint=marker,
        flow_run_id=f"{marker}-flow-{index}",
        provider_code="memory",
        page_limit=6000,
        backfill_batch_id=marker if retry_run_id is None else None,
        retry_run_id=retry_run_id,
    )


def test_succeeded_run_is_not_reopenable(
    repository: SqlAlchemyMarketDataRepository,
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    marker = uuid4().hex
    claim = _claim(repository, marker=marker)
    repository.publish_success(claim, SyncCounts(received_count=1, valid_count=1))
    reopened = _claim(repository, marker=marker, index=1, run_key=f"{marker}-0")
    assert reopened.already_succeeded
    with sqlite_session_factory() as session:
        runs = session.scalars(
            select(MarketDataSyncRun).where(MarketDataSyncRun.run_key == f"{marker}-0")
        ).all()
        attempts = session.scalars(
            select(MarketDataSyncAttempt).where(MarketDataSyncAttempt.run_id == claim.run_id)
        ).all()
    assert len(runs) == 1
    assert runs[0].status == "SUCCEEDED"
    assert len(attempts) == 1


def test_retry_reuses_same_run_and_marks_expired_attempt_abandoned(
    repository: SqlAlchemyMarketDataRepository,
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    marker = uuid4().hex
    claim = _claim(repository, marker=marker)
    with sqlite_session_factory.begin() as session:
        session.execute(
            update(MarketDataSyncAttempt)
            .where(MarketDataSyncAttempt.attempt_id == claim.attempt_id)
            .values(lease_expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
        )
    retry = _claim(repository, marker=marker, index=1, retry_run_id=claim.run_id)
    assert retry.run_id == claim.run_id
    assert retry.attempt_no == 2
    with sqlite_session_factory() as session:
        old_attempt = session.scalar(
            select(MarketDataSyncAttempt).where(
                MarketDataSyncAttempt.attempt_id == claim.attempt_id
            )
        )
        abandoned_issue = session.scalar(
            select(MarketDataSyncIssue).where(
                MarketDataSyncIssue.attempt_id == claim.attempt_id,
                MarketDataSyncIssue.category == "ABANDONED",
            )
        )
    assert old_attempt is not None
    assert old_attempt.status == "ABANDONED"
    assert abandoned_issue is not None


def test_active_lease_blocks_second_attempt(
    repository: SqlAlchemyMarketDataRepository,
) -> None:
    marker = uuid4().hex
    claim = _claim(repository, marker=marker)
    with pytest.raises(RuntimeError, match="已有未过期执行尝试"):
        _claim(repository, marker=marker, index=1, retry_run_id=claim.run_id)


def test_retry_of_succeeded_run_is_noop(
    repository: SqlAlchemyMarketDataRepository,
) -> None:
    marker = uuid4().hex
    claim = _claim(repository, marker=marker)
    repository.publish_success(claim, SyncCounts(received_count=1, valid_count=1))
    reopened = _claim(repository, marker=marker, index=1, retry_run_id=claim.run_id)
    assert reopened.already_succeeded
    assert reopened.attempt_no == claim.attempt_no


def test_failure_records_counts_issues_and_error_summary(
    repository: SqlAlchemyMarketDataRepository,
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    marker = uuid4().hex
    claim = _claim(repository, marker=marker)
    repository.record_failure(
        claim,
        SyncCounts(received_count=100, valid_count=0),
        category="PROVIDER_RATE_LIMITED",
        summary="上游短时频率限制",
    )
    with sqlite_session_factory() as session:
        run = session.scalar(
            select(MarketDataSyncRun).where(MarketDataSyncRun.run_id == claim.run_id)
        )
        attempt = session.scalar(
            select(MarketDataSyncAttempt).where(
                MarketDataSyncAttempt.attempt_id == claim.attempt_id
            )
        )
        issue = session.scalar(
            select(MarketDataSyncIssue).where(
                MarketDataSyncIssue.attempt_id == claim.attempt_id
            )
        )
    assert run is not None and run.status == "FAILED"
    assert attempt is not None
    assert attempt.status == "FAILED"
    assert attempt.received_count == 100
    assert attempt.error_category == "PROVIDER_RATE_LIMITED"
    assert attempt.error_summary == "上游短时频率限制"
    assert issue is not None and issue.category == "PROVIDER_RATE_LIMITED"
