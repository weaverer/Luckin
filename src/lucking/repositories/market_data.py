"""MySQL 审计 Repository：权威运行、不可变尝试、固定租约与脱敏问题。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    MarketDataAttemptStatus,
    MarketDataSyncAttempt,
    MarketDataSyncIssue,
    MarketDataSyncRun,
    MarketDataSyncStatus,
    VenueCode,
)
from lucking.models.stock_list import StockCurrent, StockProviderMapping

# 统一问题类别全集（data-model.md §10）。
MARKET_DATA_ISSUE_CATEGORIES: frozenset[str] = frozenset(
    {
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_DEADLINE",
        "AUTHENTICATION",
        "QUOTA_EXCEEDED",
        "EMPTY_AGGREGATE",
        "RESPONSE_CAPPED",
        "CONTINUATION_INCOMPLETE",
        "PAGINATION_NOT_VERIFIED",
        "PAGINATION_NOT_ADVANCING",
        "REPEATED_PAGE",
        "MAX_PAGES_EXCEEDED",
        "TRADE_DATE_MISMATCH",
        "PERIOD_MISMATCH",
        "INVALID_FIELD",
        "UNKNOWN_STOCK_IDENTITY",
        "UNKNOWN_INDEX_IDENTITY",
        "IDENTITY_CONFLICT",
        "DUPLICATE",
        "RECORD_CONFLICT",
        "ABANDONED",
        "PERSISTENCE_ERROR",
    }
)


class MarketDataValidationError(RuntimeError):
    """领域校验失败；category 必须属于统一问题类别。"""

    def __init__(self, category: str, summary: str) -> None:
        if category not in MARKET_DATA_ISSUE_CATEGORIES:
            raise ValueError(f"未知问题类别：{category}")
        self.category = category
        self.summary = summary[:500]
        super().__init__(f"{category}: {self.summary}")


class BackfillDateAction(StrEnum):
    START = "START"
    SKIP_SUCCEEDED = "SKIP_SUCCEEDED"
    RETRY = "RETRY"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class AttemptClaim:
    run_id: str
    run_key: str
    attempt_id: str
    attempt_no: int
    data_kind: str
    target_trade_date: date
    run_kind: str
    backfill_batch_id: str | None
    already_succeeded: bool = False


@dataclass(frozen=True, slots=True)
class BackfillDateResolution:
    action: BackfillDateAction
    run_id: str | None
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class RunDiagnostics:
    run_id: str
    data_kind: str
    run_kind: str
    target_trade_date: date
    status: str
    attempt_count: int
    schedule_slug: str | None
    backfill_batch_id: str | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttemptDiagnostics:
    attempt_id: str
    run_id: str
    attempt_no: int
    status: str
    flow_run_id: str
    started_at: datetime
    completed_at: datetime | None
    received_count: int
    valid_count: int
    error_category: str | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class IssueDiagnostics:
    issue_id: str
    attempt_id: str
    category: str
    venue_code: str | None
    security_code: str | None
    field_name: str | None
    safe_summary: str


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    provider_code: str
    provider_security_id: str
    venue_code: VenueCode
    security_code: str


@dataclass(frozen=True, slots=True)
class ResolvedStockIdentity:
    stock_id: str
    venue_code: VenueCode
    security_code: str


@dataclass(frozen=True, slots=True)
class SyncCounts:
    provider_request_count: int = 0
    provider_retry_count: int = 0
    provider_page_count: int = 0
    provider_page_limit: int = 6000
    provider_last_page_count: int = 0
    received_count: int = 0
    valid_count: int = 0
    added_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    conflict_count: int = 0
    candidate_digest: str | None = None


@dataclass(frozen=True, slots=True)
class SyncIssue:
    category: str
    safe_summary: str
    provider_security_id_hash: str | None = None
    venue_code: str | None = None
    security_code: str | None = None
    field_name: str | None = None
    payload_hash: str | None = None


class MarketDataRepository(Protocol):
    def claim_run_and_start_attempt(
        self,
        *,
        run_key: str,
        run_kind: str,
        data_kind: str,
        target_trade_date: date,
        scope_fingerprint: str,
        flow_run_id: str,
        provider_code: str,
        page_limit: int,
        schedule_slug: str | None = None,
        scheduled_for: datetime | None = None,
        backfill_batch_id: str | None = None,
        retry_run_id: str | None = None,
    ) -> AttemptClaim: ...

    def resolve_stock_identity(
        self, candidate: IdentityCandidate
    ) -> ResolvedStockIdentity | None: ...

    def publish_success(
        self,
        claim: AttemptClaim,
        counts: SyncCounts,
        issues: tuple[SyncIssue, ...] = (),
    ) -> SyncCounts: ...

    def record_failure(
        self,
        claim: AttemptClaim,
        counts: SyncCounts,
        *,
        category: str,
        summary: str,
        issues: tuple[SyncIssue, ...] = (),
    ) -> None: ...

    def resolve_backfill_date(
        self, *, data_kind: str, backfill_batch_id: str, target_trade_date: date
    ) -> BackfillDateResolution: ...

    def list_runs(
        self,
        *,
        data_kind: str | None = None,
        target_trade_date: date | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[RunDiagnostics, ...]: ...

    def list_attempts(
        self,
        *,
        run_id: str | None = None,
        error_category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[AttemptDiagnostics, ...]: ...

    def list_issues(
        self,
        *,
        attempt_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[IssueDiagnostics, ...]: ...


class SqlAlchemyMarketDataRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        lease_seconds: int = 2100,
    ) -> None:
        if lease_seconds != 2100:
            raise ValueError("运行租约固定为 2100 秒")
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds

    def claim_run_and_start_attempt(
        self,
        *,
        run_key: str,
        run_kind: str,
        data_kind: str,
        target_trade_date: date,
        scope_fingerprint: str,
        flow_run_id: str,
        provider_code: str,
        page_limit: int,
        schedule_slug: str | None = None,
        scheduled_for: datetime | None = None,
        backfill_batch_id: str | None = None,
        retry_run_id: str | None = None,
    ) -> AttemptClaim:
        session = self._session_factory()
        try:
            with session.begin():
                existing_attempt = session.scalar(
                    select(MarketDataSyncAttempt).where(
                        MarketDataSyncAttempt.flow_run_id == flow_run_id
                    )
                )
                if existing_attempt is not None:
                    run = session.scalar(
                        select(MarketDataSyncRun).where(
                            MarketDataSyncRun.run_id == existing_attempt.run_id
                        )
                    )
                    assert run is not None
                    return _claim_from(run, existing_attempt)

                if retry_run_id is not None:
                    run = session.scalar(
                        select(MarketDataSyncRun)
                        .where(MarketDataSyncRun.run_id == retry_run_id)
                        .with_for_update()
                    )
                    if run is None:
                        raise ValueError("重试 run_id 不存在")
                else:
                    run = session.scalar(
                        select(MarketDataSyncRun)
                        .where(MarketDataSyncRun.run_key == run_key)
                        .with_for_update()
                    )
                    if run is None:
                        run = MarketDataSyncRun(
                            run_id=str(uuid4()),
                            run_key=run_key,
                            data_kind=data_kind,
                            run_kind=run_kind,
                            schedule_slug=schedule_slug,
                            scheduled_for=scheduled_for,
                            backfill_batch_id=backfill_batch_id,
                            target_trade_date=target_trade_date,
                            scope_fingerprint=scope_fingerprint,
                            status=MarketDataSyncStatus.PENDING,
                            attempt_count=0,
                        )
                        session.add(run)
                        session.flush()

                if run.status == MarketDataSyncStatus.SUCCEEDED:
                    attempt = session.scalar(
                        select(MarketDataSyncAttempt).where(
                            MarketDataSyncAttempt.attempt_id == run.successful_attempt_id
                        )
                    )
                    assert attempt is not None
                    return replace(_claim_from(run, attempt), already_succeeded=True)

                now = _database_utc_now(session)
                active = session.scalar(
                    select(MarketDataSyncAttempt)
                    .where(
                        MarketDataSyncAttempt.run_id == run.run_id,
                        MarketDataSyncAttempt.status == MarketDataAttemptStatus.RUNNING,
                    )
                    .order_by(MarketDataSyncAttempt.attempt_no.desc())
                    .with_for_update()
                )
                if active is not None:
                    if active.lease_expires_at > now:
                        raise RuntimeError("运行已有未过期执行尝试")
                    active.status = MarketDataAttemptStatus.ABANDONED
                    active.completed_at = now
                    session.add(
                        MarketDataSyncIssue(
                            issue_id=str(uuid4()),
                            attempt_id=active.attempt_id,
                            category="ABANDONED",
                            safe_summary="固定运行租约已过期",
                        )
                    )
                if retry_run_id is not None and run.status not in (
                    MarketDataSyncStatus.FAILED,
                    MarketDataSyncStatus.RUNNING,
                ):
                    raise ValueError("只有失败或租约过期的运行可以重试")
                run.status = MarketDataSyncStatus.RUNNING
                run.attempt_count += 1
                attempt = MarketDataSyncAttempt(
                    attempt_id=str(uuid4()),
                    run_id=run.run_id,
                    attempt_no=run.attempt_count,
                    flow_run_id=flow_run_id,
                    provider_code=provider_code,
                    status=MarketDataAttemptStatus.RUNNING,
                    started_at=now,
                    lease_expires_at=now + timedelta(seconds=self._lease_seconds),
                    provider_page_limit=page_limit,
                )
                session.add(attempt)
                session.flush()
                return _claim_from(run, attempt)
        except IntegrityError:
            session.rollback()
            with session.begin():
                run = session.scalar(
                    select(MarketDataSyncRun).where(
                        MarketDataSyncRun.run_key == run_key
                    )
                )
                attempt = (
                    session.scalar(
                        select(MarketDataSyncAttempt)
                        .where(MarketDataSyncAttempt.run_id == run.run_id)
                        .order_by(MarketDataSyncAttempt.attempt_no.desc())
                    )
                    if run is not None
                    else None
                )
                if run is None or attempt is None:
                    raise
                return _claim_from(run, attempt)
        finally:
            session.close()

    def resolve_stock_identity(self, candidate: IdentityCandidate) -> ResolvedStockIdentity | None:
        with self._session_factory() as session:
            mapped_stock_id = session.scalar(
                select(StockProviderMapping.stock_id).where(
                    StockProviderMapping.provider_code == candidate.provider_code,
                    StockProviderMapping.provider_security_id == candidate.provider_security_id,
                )
            )
            canonical_rows = list(
                session.scalars(
                    select(StockCurrent).where(
                        StockCurrent.market_code == "CN-S",
                        StockCurrent.venue_code == candidate.venue_code.value,
                        StockCurrent.security_code == candidate.security_code,
                    )
                )
            )
            if not canonical_rows:
                return None
            if len(canonical_rows) > 1:
                raise MarketDataValidationError("IDENTITY_CONFLICT", "规范键不唯一")
            canonical = canonical_rows[0]
            if mapped_stock_id is not None and mapped_stock_id != canonical.stock_id:
                raise MarketDataValidationError("IDENTITY_CONFLICT", "Provider 映射与规范键不一致")
            return ResolvedStockIdentity(
                canonical.stock_id,
                VenueCode(canonical.venue_code),
                canonical.security_code,
            )

    def publish_success(
        self,
        claim: AttemptClaim,
        counts: SyncCounts,
        issues: tuple[SyncIssue, ...] = (),
    ) -> SyncCounts:
        session = self._session_factory()
        try:
            with session.begin():
                run, attempt = self._lock_owned_attempt(session, claim)
                now = _database_utc_now(session)
                _apply_counts(attempt, counts)
                for issue in issues:
                    session.add(
                        MarketDataSyncIssue(
                            issue_id=str(uuid4()),
                            attempt_id=attempt.attempt_id,
                            **asdict(issue),
                        )
                    )
                attempt.status = MarketDataAttemptStatus.SUCCEEDED
                attempt.completed_at = now
                run.status = MarketDataSyncStatus.SUCCEEDED
                run.successful_attempt_id = attempt.attempt_id
                run.published_at = now
                return counts
        finally:
            session.close()

    def record_failure(
        self,
        claim: AttemptClaim,
        counts: SyncCounts,
        *,
        category: str,
        summary: str,
        issues: tuple[SyncIssue, ...] = (),
    ) -> None:
        with self._session_factory.begin() as session:
            run, attempt = self._lock_owned_attempt(session, claim)
            if attempt.status != MarketDataAttemptStatus.RUNNING:
                return
            _apply_counts(attempt, counts)
            now = _database_utc_now(session)
            attempt.status = MarketDataAttemptStatus.FAILED
            attempt.completed_at = now
            attempt.error_category = category[:48]
            attempt.error_summary = summary[:500]
            run.status = MarketDataSyncStatus.FAILED
            for issue in issues or (SyncIssue(category, summary),):
                session.add(
                    MarketDataSyncIssue(
                        issue_id=str(uuid4()),
                        attempt_id=attempt.attempt_id,
                        **asdict(issue),
                    )
                )

    def resolve_backfill_date(
        self, *, data_kind: str, backfill_batch_id: str, target_trade_date: date
    ) -> BackfillDateResolution:
        with self._session_factory() as session:
            run = session.scalar(
                select(MarketDataSyncRun).where(
                    MarketDataSyncRun.run_kind == "BACKFILL",
                    MarketDataSyncRun.data_kind == data_kind,
                    MarketDataSyncRun.backfill_batch_id == backfill_batch_id,
                    MarketDataSyncRun.target_trade_date == target_trade_date,
                )
            )
            if run is None:
                return BackfillDateResolution(
                    BackfillDateAction.START, None, target_trade_date
                )
            if run.status == MarketDataSyncStatus.SUCCEEDED:
                return BackfillDateResolution(
                    BackfillDateAction.SKIP_SUCCEEDED, run.run_id, target_trade_date
                )
            if run.status == MarketDataSyncStatus.FAILED:
                return BackfillDateResolution(
                    BackfillDateAction.RETRY, run.run_id, target_trade_date
                )
            attempt = session.scalar(
                select(MarketDataSyncAttempt)
                .where(
                    MarketDataSyncAttempt.run_id == run.run_id,
                    MarketDataSyncAttempt.status == MarketDataAttemptStatus.RUNNING,
                )
                .order_by(MarketDataSyncAttempt.attempt_no.desc())
            )
            expired = attempt is None or attempt.lease_expires_at <= _database_utc_now(session)
            return BackfillDateResolution(
                BackfillDateAction.RETRY if expired else BackfillDateAction.IN_PROGRESS,
                run.run_id,
                target_trade_date,
            )

    def list_runs(
        self,
        *,
        data_kind: str | None = None,
        target_trade_date: date | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[RunDiagnostics, ...]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("分页参数非法")
        filters = []
        if data_kind is not None:
            filters.append(MarketDataSyncRun.data_kind == data_kind)
        if target_trade_date is not None:
            filters.append(MarketDataSyncRun.target_trade_date == target_trade_date)
        if status is not None:
            filters.append(MarketDataSyncRun.status == status)
        with self._session_factory() as session:
            rows = session.scalars(
                select(MarketDataSyncRun)
                .where(*filters)
                .order_by(
                    MarketDataSyncRun.target_trade_date.desc(),
                    MarketDataSyncRun.created_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            return tuple(
                RunDiagnostics(
                    run.run_id,
                    run.data_kind,
                    run.run_kind,
                    run.target_trade_date,
                    run.status,
                    run.attempt_count,
                    run.schedule_slug,
                    run.backfill_batch_id,
                    run.published_at,
                )
                for run in rows
            )

    def list_attempts(
        self,
        *,
        run_id: str | None = None,
        error_category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[AttemptDiagnostics, ...]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("分页参数非法")
        filters = []
        if run_id is not None:
            filters.append(MarketDataSyncAttempt.run_id == run_id)
        if error_category is not None:
            filters.append(MarketDataSyncAttempt.error_category == error_category)
        with self._session_factory() as session:
            rows = session.scalars(
                select(MarketDataSyncAttempt)
                .where(*filters)
                .order_by(MarketDataSyncAttempt.started_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return tuple(
                AttemptDiagnostics(
                    attempt.attempt_id,
                    attempt.run_id,
                    attempt.attempt_no,
                    attempt.status,
                    attempt.flow_run_id,
                    attempt.started_at,
                    attempt.completed_at,
                    attempt.received_count,
                    attempt.valid_count,
                    attempt.error_category,
                    attempt.error_summary,
                )
                for attempt in rows
            )

    def list_issues(
        self,
        *,
        attempt_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[IssueDiagnostics, ...]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("分页参数非法")
        filters = []
        if attempt_id is not None:
            filters.append(MarketDataSyncIssue.attempt_id == attempt_id)
        if category is not None:
            filters.append(MarketDataSyncIssue.category == category)
        with self._session_factory() as session:
            rows = session.scalars(
                select(MarketDataSyncIssue)
                .where(*filters)
                .order_by(MarketDataSyncIssue.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return tuple(
                IssueDiagnostics(
                    issue.issue_id,
                    issue.attempt_id,
                    issue.category,
                    issue.venue_code,
                    issue.security_code,
                    issue.field_name,
                    issue.safe_summary,
                )
                for issue in rows
            )

    @staticmethod
    def _lock_owned_attempt(
        session: Session, claim: AttemptClaim
    ) -> tuple[MarketDataSyncRun, MarketDataSyncAttempt]:
        run = session.scalar(
            select(MarketDataSyncRun)
            .where(MarketDataSyncRun.run_id == claim.run_id)
            .with_for_update()
        )
        attempt = session.scalar(
            select(MarketDataSyncAttempt)
            .where(MarketDataSyncAttempt.attempt_id == claim.attempt_id)
            .with_for_update()
        )
        if run is None or attempt is None:
            raise RuntimeError("运行或尝试不存在")
        if (
            run.status != MarketDataSyncStatus.RUNNING
            or attempt.status != MarketDataAttemptStatus.RUNNING
        ):
            raise RuntimeError("运行或尝试不再可写")
        return run, attempt


def _database_utc_now(session: Session) -> datetime:
    if session.bind is not None and session.bind.dialect.name == "mysql":
        value = session.scalar(select(func.utc_timestamp(6)))
        assert isinstance(value, datetime)
        return value
    return datetime.now(UTC).replace(tzinfo=None)


def _claim_from(
    run: MarketDataSyncRun, attempt: MarketDataSyncAttempt
) -> AttemptClaim:
    return AttemptClaim(
        run.run_id,
        run.run_key,
        attempt.attempt_id,
        attempt.attempt_no,
        run.data_kind,
        run.target_trade_date,
        run.run_kind,
        run.backfill_batch_id,
    )


def _apply_counts(attempt: MarketDataSyncAttempt, counts: SyncCounts) -> None:
    for field in counts.__dataclass_fields__:
        setattr(attempt, field, getattr(counts, field))
