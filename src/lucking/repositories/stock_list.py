"""Stock-list repository contract and SQLAlchemy persistence implementation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.db import as_utc_naive
from lucking.models.stock_list import (
    StockCurrent,
    StockListSyncIssue,
    StockListSyncRun,
    StockProviderMapping,
    SyncStatus,
)
from lucking.ports.stock_list_provider import (
    ListingStatus,
    ProviderStockRecord,
    RetrievalEvidence,
    VenueCode,
)


@dataclass(frozen=True, slots=True)
class RunClaim:
    run_id: str
    run_key: str
    status: SyncStatus
    attempt_count: int
    should_execute: bool


@dataclass(frozen=True, slots=True)
class StockListItem:
    stock_id: str
    market_code: str
    venue_code: VenueCode
    security_code: str
    display_name: str
    currency_code: str
    listing_status: ListingStatus
    listed_on: date | None
    delisted_on: date | None


@dataclass(frozen=True, slots=True)
class PublishRecord:
    stock_id: str
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    display_name: str
    currency_code: str
    listing_status: ListingStatus
    listed_on: date | None
    delisted_on: date | None
    is_new: bool
    is_changed: bool


class StockListRepository(Protocol):
    def claim_run(
        self,
        *,
        run_key: str,
        schedule_slug: str,
        scheduled_for: datetime,
        business_date: date,
        scope_fingerprint: str,
        provider_code: str,
        flow_run_id: str,
        started_at: datetime,
        is_manual_retry: bool,
    ) -> RunClaim: ...

    def provider_mappings(self, provider_code: str) -> dict[str, str]: ...

    def resolve_records(
        self, provider_code: str, records: tuple[object, ...]
    ) -> tuple[PublishRecord, ...]: ...

    def publish_success(
        self,
        claim: RunClaim,
        *,
        provider_code: str,
        records: tuple[PublishRecord, ...],
        evidence: object,
        duplicate_count: int,
        candidate_digest: str,
        completed_at: datetime,
    ) -> None: ...

    def record_failure(
        self,
        claim: RunClaim,
        *,
        category: str,
        summary: str,
        completed_at: datetime,
        issues: tuple[object, ...] = (),
    ) -> None: ...

    def list_current(
        self,
        *,
        venue_code: VenueCode | None = None,
        listing_status: ListingStatus | None = None,
        security_code: str | None = None,
        name_query: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StockListItem]: ...


class SqlAlchemyStockListRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        running_lease_seconds: int = 1800,
    ) -> None:
        self._session_factory = session_factory
        self._running_lease_seconds = running_lease_seconds

    def claim_run(
        self,
        *,
        run_key: str,
        schedule_slug: str,
        scheduled_for: datetime,
        business_date: date,
        scope_fingerprint: str,
        provider_code: str,
        flow_run_id: str,
        started_at: datetime,
        is_manual_retry: bool,
    ) -> RunClaim:
        now = as_utc_naive(started_at)
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(StockListSyncRun)
                .where(StockListSyncRun.run_key == run_key)
                .with_for_update()
            )
            if row is None:
                row = StockListSyncRun(
                    run_id=str(uuid4()),
                    run_key=run_key,
                    schedule_slug=schedule_slug,
                    scheduled_for=as_utc_naive(scheduled_for),
                    business_date=business_date,
                    scope_code="CN-S",
                    scope_fingerprint=scope_fingerprint,
                    provider_code=provider_code,
                    flow_run_id=flow_run_id,
                    status=SyncStatus.RUNNING.value,
                    attempt_count=1,
                    started_at=now,
                    completed_at=None,
                    published_at=None,
                    segment_count=12,
                    completed_segment_count=0,
                    capped_segment_count=0,
                    received_count=0,
                    valid_count=0,
                    duplicate_count=0,
                    invalid_count=0,
                    conflict_count=0,
                    baseline_count=None,
                    added_count=0,
                    updated_count=0,
                    unchanged_count=0,
                    candidate_digest=None,
                    error_category=None,
                    error_summary=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                return RunClaim(row.run_id, run_key, SyncStatus.RUNNING, 1, True)
            status = SyncStatus(row.status)
            if status is SyncStatus.SUCCEEDED:
                return RunClaim(row.run_id, run_key, status, row.attempt_count, False)
            if row.flow_run_id == flow_run_id and status is SyncStatus.RUNNING:
                return RunClaim(row.run_id, run_key, status, row.attempt_count, False)
            if status is SyncStatus.FAILED and not is_manual_retry:
                return RunClaim(row.run_id, run_key, status, row.attempt_count, False)
            if status is SyncStatus.RUNNING:
                lease_expired = (
                    row.started_at is not None
                    and (now - row.started_at).total_seconds()
                    > self._running_lease_seconds
                )
                if not is_manual_retry or not lease_expired:
                    return RunClaim(
                        row.run_id, run_key, status, row.attempt_count, False
                    )
                session.add(
                    StockListSyncIssue(
                        issue_id=str(uuid4()),
                        run_id=row.run_id,
                        attempt_no=row.attempt_count,
                        category="ABANDONED",
                        provider_security_id_hash=None,
                        venue_code=None,
                        security_code=None,
                        field_name=None,
                        safe_summary="运行租约过期，允许显式补跑",
                        payload_hash=None,
                        created_at=now,
                    )
                )
            row.status = SyncStatus.RUNNING.value
            row.flow_run_id = flow_run_id
            row.attempt_count += 1
            row.started_at = now
            row.completed_at = None
            row.error_category = None
            row.error_summary = None
            row.updated_at = now
            return RunClaim(
                row.run_id, run_key, SyncStatus.RUNNING, row.attempt_count, True
            )

    def get_run_result(self, run_id: str) -> StockListSyncRun | None:
        with self._session_factory() as session:
            return session.get(StockListSyncRun, run_id)

    def provider_mappings(self, provider_code: str) -> dict[str, str]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(StockProviderMapping).where(
                    StockProviderMapping.provider_code == provider_code
                )
            )
            return {row.provider_security_id: row.stock_id for row in rows}

    def resolve_records(
        self, provider_code: str, records: tuple[object, ...]
    ) -> tuple[PublishRecord, ...]:
        typed = tuple(cast(ProviderStockRecord, record) for record in records)
        with self._session_factory() as session:
            mapping_rows = session.scalars(
                select(StockProviderMapping).where(
                    StockProviderMapping.provider_code == provider_code
                )
            )
            mappings = {row.provider_security_id: row.stock_id for row in mapping_rows}
            current_rows = session.scalars(select(StockCurrent))
            by_id = {row.stock_id: row for row in current_rows}
            by_key = {
                (row.market_code, row.venue_code, row.security_code): row
                for row in by_id.values()
            }
        result: list[PublishRecord] = []
        for record in typed:
            mapped_id = mappings.get(record.provider_security_id)
            keyed = by_key.get(("CN-S", record.venue_code.value, record.security_code))
            if mapped_id and keyed and mapped_id != keyed.stock_id:
                raise ValueError("Provider 映射与规范身份冲突")
            existing = by_id.get(mapped_id) if mapped_id else keyed
            stock_id = existing.stock_id if existing else str(uuid4())
            changed = existing is None or any(
                (
                    existing.venue_code != record.venue_code.value,
                    existing.security_code != record.security_code,
                    existing.display_name != record.display_name,
                    existing.currency_code != record.currency_code,
                    existing.listing_status != record.listing_status.value,
                    existing.listed_on != record.listed_on,
                    existing.delisted_on != record.delisted_on,
                )
            )
            result.append(
                PublishRecord(
                    stock_id,
                    record.provider_security_id,
                    record.venue_code,
                    record.security_code,
                    record.display_name,
                    record.currency_code,
                    record.listing_status,
                    record.listed_on,
                    record.delisted_on,
                    existing is None,
                    changed,
                )
            )
        return tuple(result)

    def publish_success(
        self,
        claim: RunClaim,
        *,
        provider_code: str,
        records: tuple[PublishRecord, ...],
        evidence: object,
        duplicate_count: int,
        candidate_digest: str,
        completed_at: datetime,
    ) -> None:
        typed_evidence = cast(RetrievalEvidence, evidence)
        now = as_utc_naive(completed_at)
        with self._session_factory.begin() as session:
            run = session.scalar(
                select(StockListSyncRun)
                .where(StockListSyncRun.run_id == claim.run_id)
                .with_for_update()
            )
            if run is None or run.status != SyncStatus.RUNNING.value:
                raise RuntimeError("同步周期不处于可发布状态")
            for item in records:
                current = session.get(StockCurrent, item.stock_id)
                if current is None:
                    current = StockCurrent(
                        stock_id=item.stock_id,
                        market_code="CN-S",
                        venue_code=item.venue_code.value,
                        security_code=item.security_code,
                        display_name=item.display_name,
                        currency_code=item.currency_code,
                        listing_status=item.listing_status.value,
                        listed_on=item.listed_on,
                        delisted_on=item.delisted_on,
                        last_seen_run_id=claim.run_id,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(current)
                else:
                    current.venue_code = item.venue_code.value
                    current.security_code = item.security_code
                    current.display_name = item.display_name
                    current.currency_code = item.currency_code
                    current.listing_status = item.listing_status.value
                    current.listed_on = item.listed_on
                    current.delisted_on = item.delisted_on
                    current.last_seen_run_id = claim.run_id
                    current.last_seen_at = now
                    current.updated_at = now
                mapping = session.get(
                    StockProviderMapping,
                    {
                        "provider_code": provider_code,
                        "provider_security_id": item.provider_security_id,
                    },
                )
                if mapping is None:
                    session.add(
                        StockProviderMapping(
                            provider_code=provider_code,
                            provider_security_id=item.provider_security_id,
                            stock_id=item.stock_id,
                            last_seen_run_id=claim.run_id,
                            last_seen_at=now,
                            created_at=now,
                        )
                    )
                else:
                    mapping.stock_id = item.stock_id
                    mapping.last_seen_run_id = claim.run_id
                    mapping.last_seen_at = now
            run.status = SyncStatus.SUCCEEDED.value
            run.completed_at = now
            run.published_at = now
            run.segment_count = typed_evidence.segment_count
            run.completed_segment_count = typed_evidence.completed_segment_count
            run.capped_segment_count = typed_evidence.capped_segment_count
            run.received_count = typed_evidence.received_count
            run.valid_count = len(records)
            run.duplicate_count = duplicate_count
            run.invalid_count = 0
            run.conflict_count = 0
            run.added_count = sum(item.is_new for item in records)
            run.updated_count = sum(
                item.is_changed and not item.is_new for item in records
            )
            run.unchanged_count = sum(not item.is_changed for item in records)
            run.candidate_digest = candidate_digest
            run.updated_at = now

    def record_failure(
        self,
        claim: RunClaim,
        *,
        category: str,
        summary: str,
        completed_at: datetime,
        issues: tuple[object, ...] = (),
    ) -> None:
        now = as_utc_naive(completed_at)
        with self._session_factory.begin() as session:
            run = session.get(StockListSyncRun, claim.run_id)
            if run is None:
                return
            run.status = SyncStatus.FAILED.value
            run.completed_at = now
            run.error_category = category[:48]
            run.error_summary = summary[:500]
            run.updated_at = now
            for issue in issues:
                data = cast(dict[str, str | None], issue)
                session.add(
                    StockListSyncIssue(
                        issue_id=str(uuid4()),
                        run_id=claim.run_id,
                        attempt_no=claim.attempt_count,
                        category=str(data.get("category") or category)[:32],
                        provider_security_id_hash=data.get(
                            "provider_security_id_hash"
                        ),
                        venue_code=data.get("venue_code"),
                        security_code=data.get("security_code"),
                        field_name=data.get("field_name"),
                        safe_summary=str(data.get("safe_summary") or summary)[:500],
                        payload_hash=data.get("payload_hash"),
                        created_at=now,
                    )
                )

    def list_current(
        self,
        *,
        venue_code: VenueCode | None = None,
        listing_status: ListingStatus | None = None,
        security_code: str | None = None,
        name_query: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[StockListItem]:
        statement = select(StockCurrent).where(StockCurrent.market_code == "CN-S")
        if venue_code is not None:
            statement = statement.where(StockCurrent.venue_code == venue_code.value)
        if listing_status is not None:
            statement = statement.where(
                StockCurrent.listing_status == listing_status.value
            )
        if security_code:
            statement = statement.where(
                StockCurrent.security_code.startswith(security_code)
            )
        if name_query:
            statement = statement.where(StockCurrent.display_name.contains(name_query))
        statement = statement.order_by(
            StockCurrent.venue_code,
            StockCurrent.security_code,
            StockCurrent.stock_id,
        ).limit(limit).offset(offset)
        with self._session_factory() as session:
            rows = list(session.scalars(statement))
        return [
            StockListItem(
                row.stock_id,
                row.market_code,
                VenueCode(row.venue_code),
                row.security_code,
                row.display_name,
                row.currency_code,
                ListingStatus(row.listing_status),
                row.listed_on,
                row.delisted_on,
            )
            for row in rows
        ]
