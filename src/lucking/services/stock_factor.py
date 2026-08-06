"""供应商无关股票技术面因子应用服务（交易日判断与同步编排）。

行为遵循 ``contracts/stock-factor-service.md`` §4（行为 1~9）：交易日判断、
run_key 认领/租约、身份解析（003 ``stock_current``/``stock_provider_mapping``
只读复用）、批次校验与字段分级冲突判定（可修订字段按来源最新值更新、
稳定字段冲突整批失败）、ClickHouse 发布、MySQL 审计终态与回补逐日幂等。
审计复用 005 的 ``SqlAlchemyMarketDataRepository``（data_kind=STOCK_FACTOR）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    DataKind,
    MarketDataRunKind,
    RetrievalEvidence,
    backfill_run_key,
    scheduled_run_key,
    scope_fingerprint,
)
from lucking.models.stock_factor import (
    REVISION_ALLOWED_FIELDS,
    STOCK_FACTOR_FIELDS,
    ProviderStockFactorBatch,
    ProviderStockFactorRecord,
    StockFactor,
    StockFactorRequest,
)
from lucking.ports.stock_factor_common import StockFactorProvider
from lucking.repositories.market_data import (
    AttemptClaim,
    AttemptDiagnostics,
    BackfillDateResolution,
    IdentityCandidate,
    IssueDiagnostics,
    MarketDataRepository,
    MarketDataValidationError,
    RunDiagnostics,
    SyncCounts,
    SyncIssue,
)
from lucking.repositories.stock_factor_clickhouse import StockFactorClickHouseRepository
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository

BACKFILL_START = date(2024, 1, 1)

_DATA_KIND = DataKind.STOCK_FACTOR


class StockFactorSyncStatus(StrEnum):
    SKIPPED = "SKIPPED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class ScheduledStockFactorSyncCommand:
    schedule_slug: str
    scheduled_for: datetime  # 必须包含时区（原计划时点）
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class BackfillStockFactorCommand:
    target_trade_date: date
    backfill_batch_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class RetryStockFactorSyncCommand:
    run_id: str
    flow_run_id: str


type StockFactorSyncCommand = (
    ScheduledStockFactorSyncCommand | BackfillStockFactorCommand | RetryStockFactorSyncCommand
)


@dataclass(frozen=True, slots=True)
class StockFactorSyncResult:
    run_kind: str
    run_id: str
    attempt_id: str
    target_trade_date: date
    status: StockFactorSyncStatus
    received_count: int
    valid_count: int
    added_count: int
    updated_count: int
    unchanged_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int
    error_category: str | None
    error_summary: str | None


class StockFactorService:
    def __init__(
        self,
        provider: StockFactorProvider,
        repository: MarketDataRepository,
        clickhouse_repository: StockFactorClickHouseRepository,
        session_factory: sessionmaker[Session],
        *,
        timezone: str = "Asia/Shanghai",
        fetch_deadline_seconds: int = 1500,
        page_limit: int = 10000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._clickhouse = clickhouse_repository
        self._session_factory = session_factory
        self._timezone = ZoneInfo(timezone)
        self._fetch_deadline_seconds = fetch_deadline_seconds
        self._page_limit = page_limit
        self._monotonic = monotonic

    def sync(self, command: StockFactorSyncCommand) -> StockFactorSyncResult:
        if isinstance(command, ScheduledStockFactorSyncCommand):
            if command.scheduled_for.tzinfo is None:
                raise ValueError("scheduled_for 必须包含时区")
            slug = command.schedule_slug.strip()
            if not slug:
                raise ValueError("schedule_slug 不能为空")
            scheduled_utc = command.scheduled_for.astimezone(UTC)
            target = scheduled_utc.astimezone(self._timezone).date()
            if not self.is_trade_day(target):
                return self._skipped(target)
            run_key = scheduled_run_key(_DATA_KIND, slug, scheduled_utc, target)
            claim = self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=MarketDataRunKind.SCHEDULED.value,
                data_kind=_DATA_KIND.value,
                target_trade_date=target,
                scope_fingerprint=scope_fingerprint(_DATA_KIND, target),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                schedule_slug=slug,
                scheduled_for=scheduled_utc.replace(tzinfo=None),
            )
        elif isinstance(command, BackfillStockFactorCommand):
            _validate_backfill_target(command.target_trade_date, self._timezone)
            batch_id = command.backfill_batch_id.strip()
            if not batch_id:
                raise ValueError("backfill_batch_id 不能为空")
            run_key = backfill_run_key(_DATA_KIND, batch_id, command.target_trade_date)
            claim = self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=MarketDataRunKind.BACKFILL.value,
                data_kind=_DATA_KIND.value,
                target_trade_date=command.target_trade_date,
                scope_fingerprint=scope_fingerprint(_DATA_KIND, command.target_trade_date),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                backfill_batch_id=batch_id,
            )
        elif isinstance(command, RetryStockFactorSyncCommand):
            run_id = command.run_id.strip()
            if not run_id:
                raise ValueError("run_id 不能为空")
            claim = self._repository.claim_run_and_start_attempt(
                run_key="",
                run_kind="",
                data_kind="",
                target_trade_date=date.min,
                scope_fingerprint="",
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                retry_run_id=run_id,
            )
        else:
            raise TypeError("不支持的同步命令")
        if claim.already_succeeded:
            return self._result(claim, SyncCounts(), None, None)
        return self._run_sync(claim)

    def _run_sync(self, claim: AttemptClaim) -> StockFactorSyncResult:
        target = claim.target_trade_date
        counts = SyncCounts(provider_page_limit=self._page_limit)
        quality_issues: tuple[SyncIssue, ...] = ()
        try:
            deadline = self._monotonic() + self._fetch_deadline_seconds
            batch = self._fetch(target, deadline)
            counts = SyncCounts(
                provider_request_count=batch.evidence.request_count,
                provider_retry_count=batch.evidence.retry_count,
                provider_page_count=batch.evidence.page_count,
                provider_page_limit=batch.evidence.page_limit,
                provider_last_page_count=batch.evidence.last_page_count,
                received_count=batch.evidence.received_count,
            )
            self._validate_batch(target, batch.records, batch.evidence, counts)
            records, duplicate_count, digest, quality_issues = self._prepare_records(
                target, batch.records, batch.isolated
            )
            counts = replace(
                counts,
                valid_count=len(records),
                duplicate_count=duplicate_count,
                invalid_count=len(quality_issues),
                candidate_digest=digest,
            )
            if not records:
                raise MarketDataValidationError("EMPTY_AGGREGATE", "没有可发布的有效记录")
            added, updated, unchanged = self._clickhouse.publish_batch(
                target, records, datetime.now(UTC)
            )
            final = replace(
                counts, added_count=added, updated_count=updated, unchanged_count=unchanged
            )
            self._repository.publish_success(claim, final, quality_issues)
            return self._result(claim, final, None, None)
        except Exception as exc:
            category = getattr(exc, "category", "PERSISTENCE_ERROR")
            summary = getattr(exc, "summary", "股票技术面因子同步失败")
            failure_issues = quality_issues or (SyncIssue(str(category), str(summary)),)
            try:
                self._repository.record_failure(
                    claim,
                    counts,
                    category=str(category),
                    summary=str(summary),
                    issues=failure_issues,
                )
            except Exception:
                pass
            raise

    def resolve_backfill_date(
        self, *, backfill_batch_id: str, target_trade_date: date
    ) -> BackfillDateResolution:
        _validate_backfill_target(target_trade_date, self._timezone)
        if not backfill_batch_id.strip():
            raise ValueError("backfill_batch_id 不能为空")
        return self._repository.resolve_backfill_date(
            data_kind=_DATA_KIND.value,
            backfill_batch_id=backfill_batch_id.strip(),
            target_trade_date=target_trade_date,
        )

    def query_stock_factors(
        self,
        *,
        stock_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._clickhouse.query_stock_factors(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    def list_runs(
        self,
        *,
        target_trade_date: date | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[RunDiagnostics, ...]:
        return self._repository.list_runs(
            data_kind=_DATA_KIND.value,
            target_trade_date=target_trade_date,
            status=status,
            limit=limit,
            offset=offset,
        )

    def list_attempts(
        self,
        *,
        run_id: str | None = None,
        error_category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[AttemptDiagnostics, ...]:
        return self._repository.list_attempts(
            run_id=run_id,
            error_category=error_category,
            limit=limit,
            offset=offset,
        )

    def list_issues(
        self,
        *,
        attempt_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[IssueDiagnostics, ...]:
        return self._repository.list_issues(
            attempt_id=attempt_id, category=category, limit=limit, offset=offset
        )

    def is_trade_day(self, target_date: date, *, market_code: str = "CN-S") -> bool:
        """复用既有交易日历（CN-S）判断目标日期是否为交易日。"""
        calendar = SqlAlchemyTradingCalendarRepository(self._session_factory).get(
            market_code, target_date
        )
        return calendar is not None and calendar.is_open

    def _fetch(self, target: date, deadline: float) -> ProviderStockFactorBatch:
        return self._provider.fetch_stock_factors(
            StockFactorRequest(target), deadline=deadline
        )

    def _validate_batch(
        self,
        target: date,
        raw_records: tuple[Any, ...],
        evidence: RetrievalEvidence,
        counts: SyncCounts,
    ) -> None:
        if counts.received_count <= 0:
            raise MarketDataValidationError("EMPTY_AGGREGATE", "来源结果为空")
        if evidence.completed_request_count != evidence.request_count:
            raise MarketDataValidationError("CONTINUATION_INCOMPLETE", "来源请求未全部完成")
        if (
            evidence.repeated_page_detected
            or not evidence.continuation_exhausted
            or not 0 <= counts.provider_last_page_count < counts.provider_page_limit
        ):
            raise MarketDataValidationError("CONTINUATION_INCOMPLETE", "来源未证明完整覆盖")
        if not all(record.trade_date == target for record in raw_records):
            raise MarketDataValidationError("TRADE_DATE_MISMATCH", "记录交易日与目标交易日不一致")

    def _prepare_records(
        self,
        target: date,
        raw_records: tuple[Any, ...],
        isolated: tuple[Any, ...] = (),
    ) -> tuple[tuple[StockFactor, ...], int, str, tuple[SyncIssue, ...]]:
        issues: list[SyncIssue] = [_issue_from_candidate(candidate) for candidate in isolated]
        unique: dict[tuple[date, str], StockFactor] = {}
        duplicate_count = 0
        for raw in raw_records:
            identity = self._repository.resolve_stock_identity(
                IdentityCandidate(
                    provider_code=self._provider.provider_code,
                    provider_security_id=raw.provider_security_id,
                    venue_code=raw.venue_code,
                    security_code=raw.security_code,
                )
            )
            if identity is None:
                issues.append(
                    SyncIssue(
                        category="UNKNOWN_STOCK_IDENTITY",
                        safe_summary="该记录股票代码未在 003 主数据注册，已跳过",
                        provider_security_id_hash=hashlib.sha256(
                            raw.provider_security_id.encode()
                        ).hexdigest(),
                        security_code=raw.provider_security_id,
                    )
                )
                continue
            canonical = _to_canonical(raw, identity)
            key = (canonical.trade_date, canonical.stock_id)
            previous = unique.get(key)
            if previous is None:
                unique[key] = canonical
            elif _same_values(previous, canonical):
                duplicate_count += 1
            elif _only_revision_differs(previous, canonical):
                # 同批内仅可修订字段差异：按来源最新值（后出现者）保留
                unique[key] = canonical
            else:
                raise MarketDataValidationError("RECORD_CONFLICT", "同一业务键存在稳定字段冲突")
        records = tuple(sorted(unique.values(), key=_record_sort_key))
        digest = _candidate_digest(records)
        return records, duplicate_count, digest, tuple(issues)

    def _result(
        self,
        claim: AttemptClaim,
        counts: SyncCounts,
        error_category: str | None,
        error_summary: str | None,
    ) -> StockFactorSyncResult:
        return StockFactorSyncResult(
            run_kind=(
                MarketDataRunKind(claim.run_kind).value
                if claim.run_kind
                else MarketDataRunKind.SCHEDULED.value
            ),
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            target_trade_date=claim.target_trade_date,
            status=StockFactorSyncStatus.SUCCEEDED,
            received_count=counts.received_count,
            valid_count=counts.valid_count,
            added_count=counts.added_count,
            updated_count=counts.updated_count,
            unchanged_count=counts.unchanged_count,
            duplicate_count=counts.duplicate_count,
            invalid_count=counts.invalid_count,
            conflict_count=counts.conflict_count,
            error_category=error_category,
            error_summary=error_summary,
        )

    def _skipped(self, target: date) -> StockFactorSyncResult:
        return StockFactorSyncResult(
            run_kind=MarketDataRunKind.SCHEDULED.value,
            run_id="",
            attempt_id="",
            target_trade_date=target,
            status=StockFactorSyncStatus.SKIPPED,
            received_count=0,
            valid_count=0,
            added_count=0,
            updated_count=0,
            unchanged_count=0,
            duplicate_count=0,
            invalid_count=0,
            conflict_count=0,
            error_category=None,
            error_summary=None,
        )


def _issue_from_candidate(candidate: Any) -> SyncIssue:
    return SyncIssue(
        category=candidate.category,
        safe_summary=candidate.safe_summary,
        provider_security_id_hash=(
            hashlib.sha256(candidate.provider_security_id.encode()).hexdigest()
            if candidate.provider_security_id is not None
            else None
        ),
        security_code=candidate.security_code,
        field_name=candidate.field_name,
    )


def _validate_backfill_target(target: date, timezone: ZoneInfo) -> None:
    if target < BACKFILL_START:
        raise ValueError("回补不得早于 2024-01-01")
    if target > datetime.now(timezone).date():
        raise ValueError("回补不得包含未来交易日")


def _to_canonical(raw: ProviderStockFactorRecord, identity: Any) -> StockFactor:
    return StockFactor(
        trade_date=raw.trade_date,
        stock_id=identity.stock_id,
        stock_code=raw.provider_security_id,
        close=raw.close,
        values=dict(raw.values),
    )


def _same_values(left: StockFactor, right: StockFactor) -> bool:
    return (
        left.stock_code == right.stock_code
        and left.close == right.close
        and left.values == right.values
    )


def _only_revision_differs(left: StockFactor, right: StockFactor) -> bool:
    if left.stock_code != right.stock_code or left.close != right.close:
        return False
    return all(
        left.values.get(field) == right.values.get(field)
        for field in STOCK_FACTOR_FIELDS
        if field not in REVISION_ALLOWED_FIELDS
    )


def _record_sort_key(record: StockFactor) -> tuple[str, str]:
    return record.stock_code, record.stock_id


def _candidate_digest(records: tuple[StockFactor, ...]) -> str:
    canonical_rows = [
        [record.trade_date.isoformat(), record.stock_id, record.stock_code]
        for record in records
    ]
    return hashlib.sha256(
        json.dumps(canonical_rows, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
