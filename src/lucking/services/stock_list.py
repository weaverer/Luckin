"""Provider-neutral stock-list synchronization and current-list query service."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from lucking.logging import safe_identifier_hash
from lucking.models.stock_list import SyncStatus
from lucking.ports.stock_list_provider import (
    FIXED_VENUES,
    ListingStatus,
    ProviderError,
    ProviderStockList,
    ProviderStockRecord,
    ScopeCode,
    StockListProvider,
    StockListRequest,
    VenueCode,
)
from lucking.repositories.stock_list import (
    RunClaim,
    StockListItem,
    StockListRepository,
)

_SCOPE_FINGERPRINT = hashlib.sha256(
    b"CN-S|XSHG,XSHE,XBSE|ACTIVE,DELISTED,SUSPENDED,PENDING"
).hexdigest()


class InvalidStockList(RuntimeError):
    category = "INVALID_STOCK_LIST"

    def __init__(
        self, message: str, *, issues: tuple[dict[str, str | None], ...] = ()
    ) -> None:
        self.issues = issues
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class StockListSyncCommand:
    schedule_slug: str
    scheduled_at: datetime
    scope_code: ScopeCode
    flow_run_id: str
    is_manual_retry: bool = False


@dataclass(frozen=True, slots=True)
class StockListSyncResult:
    run_id: str
    run_key: str
    status: SyncStatus
    attempt_count: int
    business_date: date
    provider_code: str
    received_count: int
    valid_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int
    added_count: int
    updated_count: int
    unchanged_count: int


class StockListService:
    def __init__(
        self,
        provider: StockListProvider,
        repository: StockListRepository,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        fetch_deadline_seconds: int = 1500,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._fetch_deadline_seconds = fetch_deadline_seconds

    def sync(self, command: StockListSyncCommand) -> StockListSyncResult:
        if command.scheduled_at.tzinfo is None:
            raise InvalidStockList("scheduled_at 必须包含时区")
        if command.scope_code is not ScopeCode.CN_STOCK:
            raise InvalidStockList("首期只支持 CN-S")
        scheduled_utc = command.scheduled_at.astimezone(UTC)
        run_key = hashlib.sha256(
            (
                f"{command.schedule_slug}|{scheduled_utc.isoformat()}|"
                f"{_SCOPE_FINGERPRINT}"
            ).encode()
        ).hexdigest()
        started_at = self._now().astimezone(UTC)
        business_date = command.scheduled_at.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        claim = self.repository.claim_run(
            run_key=run_key,
            schedule_slug=command.schedule_slug,
            scheduled_for=scheduled_utc,
            business_date=business_date,
            scope_fingerprint=_SCOPE_FINGERPRINT,
            provider_code=self.provider.provider_code,
            flow_run_id=command.flow_run_id,
            started_at=started_at,
            is_manual_retry=command.is_manual_retry,
        )
        if not claim.should_execute:
            return self._result_from_existing(claim, business_date)
        try:
            batch = self.provider.fetch_stock_list(
                StockListRequest(command.scope_code),
                deadline=self._monotonic() + self._fetch_deadline_seconds,
            )
            records, duplicate_count = self._validate_batch(batch)
            baseline = set(
                self.repository.provider_mappings(self.provider.provider_code)
            )
            candidates = {record.provider_security_id for record in records}
            missing = baseline - candidates
            if missing:
                raise InvalidStockList(
                    f"BASELINE_MISSING: 缺少 {len(missing)} 个既有 Provider 身份",
                    issues=tuple(
                        {
                            "category": "BASELINE_MISSING",
                            "provider_security_id_hash": safe_identifier_hash(
                                provider_id
                            ),
                            "venue_code": None,
                            "security_code": None,
                            "field_name": None,
                            "safe_summary": "上一成功结果中的 Provider 身份缺失",
                            "payload_hash": None,
                        }
                        for provider_id in sorted(missing)
                    ),
                )
            publish_records = self.repository.resolve_records(
                self.provider.provider_code, tuple(records)
            )
            digest = _candidate_digest(records)
            completed_at = self._now().astimezone(UTC)
            self.repository.publish_success(
                claim,
                provider_code=self.provider.provider_code,
                records=publish_records,
                evidence=batch.evidence,
                duplicate_count=duplicate_count,
                candidate_digest=digest,
                completed_at=completed_at,
            )
            return StockListSyncResult(
                claim.run_id,
                claim.run_key,
                SyncStatus.SUCCEEDED,
                claim.attempt_count,
                business_date,
                self.provider.provider_code,
                batch.evidence.received_count,
                len(publish_records),
                duplicate_count,
                0,
                0,
                sum(record.is_new for record in publish_records),
                sum(
                    record.is_changed and not record.is_new
                    for record in publish_records
                ),
                sum(not record.is_changed for record in publish_records),
            )
        except Exception as exc:
            category = (
                exc.category
                if isinstance(exc, (ProviderError, InvalidStockList))
                else "PERSISTENCE_ERROR"
            )
            self.repository.record_failure(
                claim,
                category=category,
                summary=str(exc)[:500],
                completed_at=self._now().astimezone(UTC),
                issues=getattr(exc, "issues", ()),
            )
            raise

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
        if not 1 <= limit <= 1000:
            raise InvalidStockList("limit 必须在 1 到 1000 之间")
        if offset < 0:
            raise InvalidStockList("offset 不得为负数")
        return self.repository.list_current(
            venue_code=venue_code,
            listing_status=listing_status,
            security_code=security_code,
            name_query=name_query,
            limit=limit,
            offset=offset,
        )

    def _validate_batch(
        self, batch: ProviderStockList
    ) -> tuple[list[ProviderStockRecord], int]:
        if batch.provider_code != self.provider.provider_code:
            raise InvalidStockList("Provider code 不匹配")
        if batch.scope_code is not ScopeCode.CN_STOCK:
            raise InvalidStockList("scope 不匹配")
        if batch.acquired_at.tzinfo is None:
            raise InvalidStockList("acquired_at 必须包含时区")
        evidence = batch.evidence
        if (
            evidence.segment_count != 12
            or evidence.completed_segment_count != 12
            or evidence.capped_segment_count != 0
            or not batch.records
        ):
            raise InvalidStockList("覆盖证明不完整")
        seen_provider: dict[str, ProviderStockRecord] = {}
        seen_identity: dict[tuple[VenueCode, str], ProviderStockRecord] = {}
        duplicate_count = 0
        for record in batch.records:
            _validate_record(record)
            provider_existing = seen_provider.get(record.provider_security_id)
            identity_key = (record.venue_code, record.security_code)
            identity_existing = seen_identity.get(identity_key)
            if provider_existing is not None or identity_existing is not None:
                existing = provider_existing or identity_existing
                if existing == record:
                    duplicate_count += 1
                    continue
                raise InvalidStockList("IDENTITY_CONFLICT: 候选身份或字段冲突")
            seen_provider[record.provider_security_id] = record
            seen_identity[identity_key] = record
        return list(seen_provider.values()), duplicate_count

    def _result_from_existing(
        self, claim: RunClaim, business_date: date
    ) -> StockListSyncResult:
        getter = getattr(self.repository, "get_run_result", None)
        row = getter(claim.run_id) if getter else None
        return StockListSyncResult(
            claim.run_id,
            claim.run_key,
            claim.status,
            claim.attempt_count,
            business_date,
            self.provider.provider_code,
            getattr(row, "received_count", 0),
            getattr(row, "valid_count", 0),
            getattr(row, "duplicate_count", 0),
            getattr(row, "invalid_count", 0),
            getattr(row, "conflict_count", 0),
            getattr(row, "added_count", 0),
            getattr(row, "updated_count", 0),
            getattr(row, "unchanged_count", 0),
        )


def _validate_record(record: ProviderStockRecord) -> None:
    if record.venue_code not in FIXED_VENUES:
        raise InvalidStockList("未知 venue")
    if not all(
        (
            record.provider_security_id,
            record.security_code,
            record.display_name,
            record.currency_code,
        )
    ):
        raise InvalidStockList("必填字段为空")
    if record.currency_code != "CNY":
        raise InvalidStockList("未知币种")
    if record.listing_status is not ListingStatus.PENDING and record.listed_on is None:
        raise InvalidStockList("状态要求上市日期")
    if record.listing_status is ListingStatus.DELISTED and record.delisted_on is None:
        raise InvalidStockList("退市状态要求退市日期")
    if (
        record.listed_on
        and record.delisted_on
        and record.delisted_on < record.listed_on
    ):
        raise InvalidStockList("退市日期早于上市日期")


def _candidate_digest(records: list[ProviderStockRecord]) -> str:
    payload = [
        {
            **asdict(record),
            "venue_code": record.venue_code.value,
            "listing_status": record.listing_status.value,
            "listed_on": record.listed_on.isoformat() if record.listed_on else None,
            "delisted_on": record.delisted_on.isoformat()
            if record.delisted_on
            else None,
        }
        for record in sorted(
            records, key=lambda item: (item.venue_code.value, item.security_code)
        )
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
