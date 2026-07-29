"""Repository contract and SQLAlchemy implementation for broker recommendations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.broker_recommendation import (
    BrokerRecommendation,
    BrokerRecommendationAttemptStatus,
    BrokerRecommendationSyncAttempt,
    BrokerRecommendationSyncIssue,
    BrokerRecommendationSyncRun,
    BrokerRecommendationSyncStatus,
)
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.ports.broker_recommendation_provider import VenueCode


class BackfillMonthAction(StrEnum):
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
    target_month: date
    run_kind: str
    backfill_batch_id: str | None
    already_succeeded: bool = False


@dataclass(frozen=True, slots=True)
class BackfillRunState:
    run_id: str
    status: str
    active_attempt_lease_expires_at: datetime | None
    active_attempt_lease_expired: bool


@dataclass(frozen=True, slots=True)
class BackfillMonthResolution:
    action: BackfillMonthAction
    run_id: str | None
    target_month: date


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
class RecommendationWrite:
    recommendation_month: date
    broker_name: str
    stock_id: str
    venue_code: VenueCode
    security_code: str
    stock_name: str


@dataclass(frozen=True, slots=True)
class SyncCounts:
    provider_request_count: int = 0
    provider_retry_count: int = 0
    provider_page_count: int = 0
    provider_page_limit: int = 1000
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
    broker_name_hash: str | None = None
    venue_code: str | None = None
    security_code: str | None = None
    field_name: str | None = None
    payload_hash: str | None = None


@dataclass(frozen=True, slots=True)
class BrokerRecommendationQuery:
    target_month: date
    broker_name: str | None = None
    stock_id: str | None = None
    venue_code: VenueCode | None = None
    security_code: str | None = None
    limit: int = 1000
    offset: int = 0


@dataclass(frozen=True, slots=True)
class BrokerRecommendationItem:
    recommendation_id: str
    recommendation_month: date
    broker_name: str
    stock_id: str
    venue_code: str
    security_code: str
    stock_name: str


class BrokerRecommendationRepository(Protocol):
    def claim_run_and_start_attempt(
        self,
        *,
        run_key: str,
        run_kind: str,
        target_month: date,
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
        records: tuple[RecommendationWrite, ...],
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

    def resolve_backfill_month(
        self, *, backfill_batch_id: str, target_month: date
    ) -> BackfillMonthResolution: ...

    def list_month(
        self, query: BrokerRecommendationQuery
    ) -> tuple[BrokerRecommendationItem, ...]: ...


class SqlAlchemyBrokerRecommendationRepository:
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
        target_month: date,
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
                    select(BrokerRecommendationSyncAttempt).where(
                        BrokerRecommendationSyncAttempt.flow_run_id == flow_run_id
                    )
                )
                if existing_attempt is not None:
                    run = session.scalar(
                        select(BrokerRecommendationSyncRun).where(
                            BrokerRecommendationSyncRun.run_id == existing_attempt.run_id
                        )
                    )
                    assert run is not None
                    return _claim_from(run, existing_attempt)

                if retry_run_id is not None:
                    run = session.scalar(
                        select(BrokerRecommendationSyncRun)
                        .where(BrokerRecommendationSyncRun.run_id == retry_run_id)
                        .with_for_update()
                    )
                    if run is None:
                        raise ValueError("重试 run_id 不存在")
                else:
                    run = session.scalar(
                        select(BrokerRecommendationSyncRun)
                        .where(BrokerRecommendationSyncRun.run_key == run_key)
                        .with_for_update()
                    )
                    if run is None:
                        run = BrokerRecommendationSyncRun(
                            run_id=str(uuid4()),
                            run_key=run_key,
                            run_kind=run_kind,
                            schedule_slug=schedule_slug,
                            scheduled_for=scheduled_for,
                            backfill_batch_id=backfill_batch_id,
                            target_month=target_month,
                            scope_fingerprint=scope_fingerprint,
                            status=BrokerRecommendationSyncStatus.PENDING,
                            attempt_count=0,
                        )
                        session.add(run)
                        session.flush()

                if run.status == BrokerRecommendationSyncStatus.SUCCEEDED:
                    attempt = session.scalar(
                        select(BrokerRecommendationSyncAttempt).where(
                            BrokerRecommendationSyncAttempt.attempt_id == run.successful_attempt_id
                        )
                    )
                    assert attempt is not None
                    return replace(_claim_from(run, attempt), already_succeeded=True)

                now = _database_utc_now(session)
                active = session.scalar(
                    select(BrokerRecommendationSyncAttempt)
                    .where(
                        BrokerRecommendationSyncAttempt.run_id == run.run_id,
                        BrokerRecommendationSyncAttempt.status
                        == BrokerRecommendationAttemptStatus.RUNNING,
                    )
                    .order_by(BrokerRecommendationSyncAttempt.attempt_no.desc())
                    .with_for_update()
                )
                if active is not None:
                    if active.lease_expires_at > now:
                        raise RuntimeError("运行已有未过期执行尝试")
                    active.status = BrokerRecommendationAttemptStatus.ABANDONED
                    active.completed_at = now
                    session.add(
                        BrokerRecommendationSyncIssue(
                            issue_id=str(uuid4()),
                            attempt_id=active.attempt_id,
                            category="ABANDONED",
                            safe_summary="固定运行租约已过期",
                        )
                    )
                if retry_run_id is not None and run.status not in (
                    BrokerRecommendationSyncStatus.FAILED,
                    BrokerRecommendationSyncStatus.RUNNING,
                ):
                    raise ValueError("只有失败或租约过期的运行可以重试")
                run.status = BrokerRecommendationSyncStatus.RUNNING
                run.attempt_count += 1
                attempt = BrokerRecommendationSyncAttempt(
                    attempt_id=str(uuid4()),
                    run_id=run.run_id,
                    attempt_no=run.attempt_count,
                    flow_run_id=flow_run_id,
                    provider_code=provider_code,
                    status=BrokerRecommendationAttemptStatus.RUNNING,
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
                    select(BrokerRecommendationSyncRun).where(
                        BrokerRecommendationSyncRun.run_key == run_key
                    )
                )
                attempt = (
                    session.scalar(
                        select(BrokerRecommendationSyncAttempt)
                        .where(BrokerRecommendationSyncAttempt.run_id == run.run_id)
                        .order_by(BrokerRecommendationSyncAttempt.attempt_no.desc())
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
            canonical = session.scalar(
                select(StockCurrent).where(
                    StockCurrent.market_code == "CN-S",
                    StockCurrent.venue_code == candidate.venue_code.value,
                    StockCurrent.security_code == candidate.security_code,
                )
            )
            if canonical is None:
                return None
            if mapped_stock_id is not None and mapped_stock_id != canonical.stock_id:
                raise ValueError("IDENTITY_CONFLICT")
            return ResolvedStockIdentity(
                canonical.stock_id,
                VenueCode(canonical.venue_code),
                canonical.security_code,
            )

    def publish_success(
        self,
        claim: AttemptClaim,
        records: tuple[RecommendationWrite, ...],
        counts: SyncCounts,
        issues: tuple[SyncIssue, ...] = (),
    ) -> SyncCounts:
        session = self._session_factory()
        try:
            with session.begin():
                run, attempt = self._lock_owned_attempt(session, claim)
                now = _database_utc_now(session)
                added = updated = unchanged = 0
                for item in records:
                    if session.bind is not None and session.bind.dialect.name == "mysql":
                        row = session.scalar(
                            select(BrokerRecommendation).where(
                                BrokerRecommendation.recommendation_month
                                == item.recommendation_month,
                                BrokerRecommendation.broker_name == item.broker_name,
                                BrokerRecommendation.stock_id == item.stock_id,
                            )
                        )
                        statement = mysql_insert(BrokerRecommendation).values(
                            recommendation_id=str(uuid4()),
                            recommendation_month=item.recommendation_month,
                            broker_name=item.broker_name,
                            stock_id=item.stock_id,
                            venue_code=item.venue_code.value,
                            security_code=item.security_code,
                            stock_name=item.stock_name,
                            first_seen_run_id=claim.run_id,
                            first_seen_at=now,
                            last_confirmed_run_id=claim.run_id,
                            last_confirmed_at=now,
                        )
                        result = cast(
                            CursorResult[Any],
                            session.execute(
                                statement.on_duplicate_key_update(
                                    venue_code=item.venue_code.value,
                                    security_code=item.security_code,
                                    stock_name=item.stock_name,
                                    last_confirmed_run_id=claim.run_id,
                                    last_confirmed_at=now,
                                )
                            ),
                        )
                        if row is None and result.rowcount == 1:
                            added += 1
                        elif row is not None and (
                            row.stock_name == item.stock_name
                            and row.venue_code == item.venue_code.value
                            and row.security_code == item.security_code
                        ):
                            unchanged += 1
                        else:
                            updated += 1
                        continue
                    row = session.scalar(
                        select(BrokerRecommendation)
                        .where(
                            BrokerRecommendation.recommendation_month == item.recommendation_month,
                            BrokerRecommendation.broker_name == item.broker_name,
                            BrokerRecommendation.stock_id == item.stock_id,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        session.add(
                            BrokerRecommendation(
                                recommendation_id=str(uuid4()),
                                recommendation_month=item.recommendation_month,
                                broker_name=item.broker_name,
                                stock_id=item.stock_id,
                                venue_code=item.venue_code.value,
                                security_code=item.security_code,
                                stock_name=item.stock_name,
                                first_seen_run_id=claim.run_id,
                                first_seen_at=now,
                                last_confirmed_run_id=claim.run_id,
                                last_confirmed_at=now,
                            )
                        )
                        added += 1
                    else:
                        changed = (
                            row.stock_name != item.stock_name
                            or row.venue_code != item.venue_code.value
                            or row.security_code != item.security_code
                        )
                        row.stock_name = item.stock_name
                        row.venue_code = item.venue_code.value
                        row.security_code = item.security_code
                        row.last_confirmed_run_id = claim.run_id
                        row.last_confirmed_at = now
                        if changed:
                            updated += 1
                        else:
                            unchanged += 1
                final = replace(
                    counts,
                    added_count=added,
                    updated_count=updated,
                    unchanged_count=unchanged,
                )
                _apply_counts(attempt, final)
                for issue in issues:
                    session.add(
                        BrokerRecommendationSyncIssue(
                            issue_id=str(uuid4()),
                            attempt_id=attempt.attempt_id,
                            **asdict(issue),
                        )
                    )
                attempt.status = BrokerRecommendationAttemptStatus.SUCCEEDED
                attempt.completed_at = now
                run.status = BrokerRecommendationSyncStatus.SUCCEEDED
                run.successful_attempt_id = attempt.attempt_id
                run.published_at = now
                return final
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
            if attempt.status != BrokerRecommendationAttemptStatus.RUNNING:
                return
            _apply_counts(attempt, counts)
            now = _database_utc_now(session)
            attempt.status = BrokerRecommendationAttemptStatus.FAILED
            attempt.completed_at = now
            attempt.error_category = category[:48]
            attempt.error_summary = summary[:500]
            run.status = BrokerRecommendationSyncStatus.FAILED
            for issue in issues or (SyncIssue(category, summary),):
                session.add(
                    BrokerRecommendationSyncIssue(
                        issue_id=str(uuid4()), attempt_id=attempt.attempt_id, **asdict(issue)
                    )
                )

    def resolve_backfill_month(
        self, *, backfill_batch_id: str, target_month: date
    ) -> BackfillMonthResolution:
        with self._session_factory() as session:
            run = session.scalar(
                select(BrokerRecommendationSyncRun).where(
                    BrokerRecommendationSyncRun.run_kind == "BACKFILL",
                    BrokerRecommendationSyncRun.backfill_batch_id == backfill_batch_id,
                    BrokerRecommendationSyncRun.target_month == target_month,
                )
            )
            if run is None:
                return BackfillMonthResolution(BackfillMonthAction.START, None, target_month)
            if run.status == BrokerRecommendationSyncStatus.SUCCEEDED:
                return BackfillMonthResolution(
                    BackfillMonthAction.SKIP_SUCCEEDED, run.run_id, target_month
                )
            if run.status == BrokerRecommendationSyncStatus.FAILED:
                return BackfillMonthResolution(BackfillMonthAction.RETRY, run.run_id, target_month)
            attempt = session.scalar(
                select(BrokerRecommendationSyncAttempt)
                .where(
                    BrokerRecommendationSyncAttempt.run_id == run.run_id,
                    BrokerRecommendationSyncAttempt.status
                    == BrokerRecommendationAttemptStatus.RUNNING,
                )
                .order_by(BrokerRecommendationSyncAttempt.attempt_no.desc())
            )
            expired = attempt is None or attempt.lease_expires_at <= _database_utc_now(session)
            return BackfillMonthResolution(
                BackfillMonthAction.RETRY if expired else BackfillMonthAction.IN_PROGRESS,
                run.run_id,
                target_month,
            )

    def list_month(self, query: BrokerRecommendationQuery) -> tuple[BrokerRecommendationItem, ...]:
        if query.target_month.day != 1:
            raise ValueError("target_month 必须是月首")
        if not 1 <= query.limit <= 1000 or query.offset < 0:
            raise ValueError("分页参数非法")
        filters = [BrokerRecommendation.recommendation_month == query.target_month]
        if query.broker_name is not None:
            filters.append(BrokerRecommendation.broker_name == query.broker_name)
        if query.stock_id is not None:
            filters.append(BrokerRecommendation.stock_id == query.stock_id)
        if query.venue_code is not None:
            filters.append(BrokerRecommendation.venue_code == query.venue_code.value)
        if query.security_code is not None:
            filters.append(BrokerRecommendation.security_code == query.security_code)
        with self._session_factory() as session:
            rows = session.scalars(
                select(BrokerRecommendation)
                .where(and_(*filters))
                .order_by(
                    BrokerRecommendation.broker_name,
                    BrokerRecommendation.venue_code,
                    BrokerRecommendation.security_code,
                    BrokerRecommendation.recommendation_id,
                )
                .limit(query.limit)
                .offset(query.offset)
            )
            return tuple(
                BrokerRecommendationItem(
                    row.recommendation_id,
                    row.recommendation_month,
                    row.broker_name,
                    row.stock_id,
                    row.venue_code,
                    row.security_code,
                    row.stock_name,
                )
                for row in rows
            )

    @staticmethod
    def _lock_owned_attempt(
        session: Session, claim: AttemptClaim
    ) -> tuple[BrokerRecommendationSyncRun, BrokerRecommendationSyncAttempt]:
        run = session.scalar(
            select(BrokerRecommendationSyncRun)
            .where(BrokerRecommendationSyncRun.run_id == claim.run_id)
            .with_for_update()
        )
        attempt = session.scalar(
            select(BrokerRecommendationSyncAttempt)
            .where(BrokerRecommendationSyncAttempt.attempt_id == claim.attempt_id)
            .with_for_update()
        )
        if run is None or attempt is None:
            raise RuntimeError("运行或尝试不存在")
        if (
            run.status != BrokerRecommendationSyncStatus.RUNNING
            or attempt.status != BrokerRecommendationAttemptStatus.RUNNING
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
    run: BrokerRecommendationSyncRun, attempt: BrokerRecommendationSyncAttempt
) -> AttemptClaim:
    return AttemptClaim(
        run.run_id,
        run.run_key,
        attempt.attempt_id,
        attempt.attempt_no,
        run.target_month,
        run.run_kind,
        run.backfill_batch_id,
    )


def _apply_counts(attempt: BrokerRecommendationSyncAttempt, counts: SyncCounts) -> None:
    for field in counts.__dataclass_fields__:
        setattr(attempt, field, getattr(counts, field))
