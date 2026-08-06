"""供应商无关股东数据应用服务（交易日判断、水位窗口与同步编排）。

行为遵循 ``contracts/shareholder-data-service.md`` §4（行为 1~11）：
交易日判断、run_key 认领/租约（DATA_KIND 按接口）、按接口/kind 水位与
公告日窗口展开、按接口提取（分页在 Provider 内部）、身份解析（003
``stock_current``/``stock_provider_mapping`` 只读复用）、批次校验与
修订/冲突判定（ann_date 锚点）、ClickHouse 发布、MySQL 审计终态与
回补逐日幂等。审计复用 005 的 ``SqlAlchemyMarketDataRepository``。
三个接口各对应一条独立链路（3 Flow 拆分，用户显式要求）：任一接口
失败只写该接口的 FAILED 终态，不影响其他两个。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
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
from lucking.models.shareholder_data import (
    ShareholderCount,
    ShareholderDataRequest,
    ShareholderHolding,
)
from lucking.ports.shareholder_data_common import ShareholderDataProvider
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
from lucking.repositories.shareholder_data_clickhouse import (
    ShareholderDataClickHouseRepository,
)
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository

BACKFILL_START = date(2024, 1, 1)

# 接口 → 审计 data_kind（3 Flow 拆分，按接口独立 run/终态）。
KIND_TO_DATA_KIND: dict[str, DataKind] = {
    "TOP10": DataKind.TOP10_HOLDERS,
    "TOP10_FLOAT": DataKind.TOP10_FLOAT_HOLDERS,
    "HOLDER_COUNT": DataKind.HOLDER_COUNT,
}


class ShareholderDataSyncStatus(StrEnum):
    SKIPPED = "SKIPPED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class ScheduledShareholderDataSyncCommand:
    schedule_slug: str
    scheduled_for: datetime  # 必须包含时区（原计划时点）
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class BackfillShareholderDataCommand:
    target_trade_date: date
    backfill_batch_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class RetryShareholderDataSyncCommand:
    run_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class ShareholderDataSyncResult:
    data_kind: str
    run_kind: str
    run_id: str
    attempt_id: str
    target_trade_date: date
    status: ShareholderDataSyncStatus
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


class ShareholderDataService:
    def __init__(
        self,
        provider: ShareholderDataProvider,
        repository: MarketDataRepository,
        clickhouse_repository: ShareholderDataClickHouseRepository,
        session_factory: sessionmaker[Session],
        *,
        timezone: str = "Asia/Shanghai",
        fetch_deadline_seconds: int = 1500,
        page_limit: int = 6000,
        window_lookback_days: int = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._clickhouse = clickhouse_repository
        self._session_factory = session_factory
        self._timezone = ZoneInfo(timezone)
        self._fetch_deadline_seconds = fetch_deadline_seconds
        self._page_limit = page_limit
        self._window_lookback_days = window_lookback_days
        self._monotonic = monotonic

    # ---- 增量（每接口一个入口）----

    def sync_top10_holders(
        self, command: ScheduledShareholderDataSyncCommand
    ) -> ShareholderDataSyncResult:
        return self._sync("TOP10", command)

    def sync_top10_float_holders(
        self, command: ScheduledShareholderDataSyncCommand
    ) -> ShareholderDataSyncResult:
        return self._sync("TOP10_FLOAT", command)

    def sync_holder_count(
        self, command: ScheduledShareholderDataSyncCommand
    ) -> ShareholderDataSyncResult:
        return self._sync("HOLDER_COUNT", command)

    # ---- 回补（每接口一个入口）----

    def backfill_top10_holders(
        self, command: BackfillShareholderDataCommand
    ) -> ShareholderDataSyncResult:
        return self._backfill("TOP10", command)

    def backfill_top10_float_holders(
        self, command: BackfillShareholderDataCommand
    ) -> ShareholderDataSyncResult:
        return self._backfill("TOP10_FLOAT", command)

    def backfill_holder_count(
        self, command: BackfillShareholderDataCommand
    ) -> ShareholderDataSyncResult:
        return self._backfill("HOLDER_COUNT", command)

    def retry(
        self, kind: str, command: RetryShareholderDataSyncCommand
    ) -> ShareholderDataSyncResult:
        """回补失败日期重试：按原 run 重新认领，只处理该目标日（FR-018）。"""
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
        if claim.already_succeeded:
            return self._result(kind, claim, SyncCounts(), None, None)
        return self._run_sync(kind, claim, (claim.target_trade_date,))

    # ---- 内部编排 ----

    def _sync(
        self, kind: str, command: ScheduledShareholderDataSyncCommand
    ) -> ShareholderDataSyncResult:
        if command.scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for 必须包含时区")
        slug = command.schedule_slug.strip()
        if not slug:
            raise ValueError("schedule_slug 不能为空")
        scheduled_utc = command.scheduled_for.astimezone(UTC)
        target = scheduled_utc.astimezone(self._timezone).date()
        if not self.is_trade_day(target):
            return self._skipped(target, kind)
        data_kind = KIND_TO_DATA_KIND[kind]
        run_key = scheduled_run_key(data_kind, slug, scheduled_utc, target)
        claim = self._repository.claim_run_and_start_attempt(
            run_key=run_key,
            run_kind=MarketDataRunKind.SCHEDULED.value,
            data_kind=data_kind.value,
            target_trade_date=target,
            scope_fingerprint=scope_fingerprint(data_kind, target),
            flow_run_id=command.flow_run_id,
            provider_code=self._provider.provider_code,
            page_limit=self._page_limit,
            schedule_slug=slug,
            scheduled_for=scheduled_utc.replace(tzinfo=None),
        )
        if claim.already_succeeded:
            return self._result(kind, claim, SyncCounts(), None, None)
        watermark = self._watermark(kind)
        window_days = self._window_days(watermark, target)
        return self._run_sync(kind, claim, window_days)

    def _backfill(
        self, kind: str, command: BackfillShareholderDataCommand
    ) -> ShareholderDataSyncResult:
        _validate_backfill_target(command.target_trade_date, self._timezone)
        batch_id = command.backfill_batch_id.strip()
        if not batch_id:
            raise ValueError("backfill_batch_id 不能为空")
        data_kind = KIND_TO_DATA_KIND[kind]
        run_key = backfill_run_key(data_kind, batch_id, command.target_trade_date)
        claim = self._repository.claim_run_and_start_attempt(
            run_key=run_key,
            run_kind=MarketDataRunKind.BACKFILL.value,
            data_kind=data_kind.value,
            target_trade_date=command.target_trade_date,
            scope_fingerprint=scope_fingerprint(data_kind, command.target_trade_date),
            flow_run_id=command.flow_run_id,
            provider_code=self._provider.provider_code,
            page_limit=self._page_limit,
            backfill_batch_id=batch_id,
        )
        if claim.already_succeeded:
            return self._result(kind, claim, SyncCounts(), None, None)
        return self._run_sync(kind, claim, (command.target_trade_date,))

    def _run_sync(
        self, kind: str, claim: AttemptClaim, days: Iterable[date]
    ) -> ShareholderDataSyncResult:
        counts = SyncCounts(provider_page_limit=self._page_limit)
        quality_issues: tuple[SyncIssue, ...] = ()
        try:
            deadline = self._monotonic() + self._fetch_deadline_seconds
            records, isolated, received = self._fetch_days(kind, tuple(days), deadline)
            counts = replace(
                counts,
                provider_request_count=received.request_count,
                provider_retry_count=received.retry_count,
                provider_page_count=received.page_count,
                provider_last_page_count=received.last_page_count,
                received_count=received.received_count,
            )
            canonical, duplicate_count, digest, quality_issues = self._prepare_records(
                kind, records, isolated
            )
            counts = replace(
                counts,
                valid_count=len(canonical),
                duplicate_count=duplicate_count,
                invalid_count=len(quality_issues),
                candidate_digest=digest,
            )
            if not canonical:
                if counts.received_count > 0:
                    # 来源有返回但全部记录无效/未映射：不得标记成功（spec ED-004）
                    raise MarketDataValidationError(
                        "EMPTY_AGGREGATE", "没有可发布的有效记录（全部无效或隔离）"
                    )
                # 窗口内无新披露属正常披露节奏（spec 边界情况修订/FR-014）：
                # 成功终态、零计数，不调用发布。
                final = replace(counts, added_count=0, updated_count=0, unchanged_count=0)
                self._repository.publish_success(claim, final, quality_issues)
                return self._result(kind, claim, final, None, None)
            updated_at = datetime.now(UTC)
            if kind == "HOLDER_COUNT":
                added, updated, unchanged = self._clickhouse.publish_counts(
                    tuple(canonical), updated_at
                )
            else:
                added, updated, unchanged = self._clickhouse.publish_holdings(
                    tuple(canonical), updated_at
                )
            final = replace(
                counts, added_count=added, updated_count=updated, unchanged_count=unchanged
            )
            self._repository.publish_success(claim, final, quality_issues)
            return self._result(kind, claim, final, None, None)
        except Exception as exc:
            category = getattr(exc, "category", "PERSISTENCE_ERROR")
            summary = getattr(exc, "summary", "股东数据同步失败")
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

    def _fetch_days(
        self, kind: str, days: tuple[date, ...], deadline: float
    ) -> tuple[
        tuple[Any, ...],
        tuple[Any, ...],
        RetrievalEvidence,
    ]:
        records: list[Any] = []
        isolated: list[Any] = []
        request_count = retry_count = page_count = last_page_count = received_count = 0
        for day in days:
            batch: Any
            if kind == "TOP10":
                batch = self._provider.fetch_top10_holders(
                    ShareholderDataRequest(day, kind), deadline=deadline
                )
            elif kind == "TOP10_FLOAT":
                batch = self._provider.fetch_top10_float_holders(
                    ShareholderDataRequest(day, kind), deadline=deadline
                )
            else:
                batch = self._provider.fetch_holder_count(
                    ShareholderDataRequest(day, kind), deadline=deadline
                )
            _validate_evidence(batch.evidence, day)
            records.extend(batch.records)
            isolated.extend(batch.isolated)
            request_count += batch.evidence.request_count
            retry_count += batch.evidence.retry_count
            page_count += batch.evidence.page_count
            last_page_count = batch.evidence.last_page_count
            received_count += batch.evidence.received_count
        evidence = RetrievalEvidence(
            request_count=request_count,
            completed_request_count=request_count,
            retry_count=retry_count,
            page_count=page_count,
            page_limit=self._page_limit,
            last_page_count=last_page_count,
            received_count=received_count,
            pagination_enabled=True,
            continuation_exhausted=True,
            repeated_page_detected=False,
        )
        return tuple(records), tuple(isolated), evidence

    def _prepare_records(
        self,
        kind: str,
        raw_records: tuple[Any, ...],
        isolated: tuple[Any, ...] = (),
    ) -> tuple[tuple[Any, ...], int, str, tuple[SyncIssue, ...]]:
        issues: list[SyncIssue] = [_issue_from_candidate(candidate) for candidate in isolated]
        # 身份键与值比较按接口分派（持仓 4 元组键 / 股东人数 2 元组键）
        unique: dict[Any, Any] = {}
        identity_key: Callable[[Any], Any]
        same_values: Callable[[Any, Any], bool]
        if kind == "HOLDER_COUNT":
            identity_key = _count_identity
            same_values = _same_count_values
        else:
            identity_key = _holding_identity
            same_values = _same_holding_values
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
            canonical = _to_canonical(kind, raw, identity)
            key = identity_key(canonical)
            previous = unique.get(key)
            if previous is None:
                unique[key] = canonical
            elif same_values(previous, canonical):
                duplicate_count += 1
            elif canonical.ann_date > previous.ann_date:
                # 同批内新公告（更正公告）→ 按最新公告保留
                unique[key] = canonical
            elif canonical.ann_date == previous.ann_date:
                # 同日重复披露（实测 2026-08-06：同一报告期同日两次公告、
                # 数值略有差异，如温一峰 3709894.0 vs 3709912.0）：保留
                # 首见记录，后见记录隔离为质量问题，不整批失败（ED-004 修订）。
                issues.append(
                    SyncIssue(
                        category="DUPLICATE_ANN_DISCLOSURE",
                        safe_summary="同一报告期同日重复披露且数值不一致，已保留首见记录",
                        security_code=canonical.stock_code,
                    )
                )
            else:
                raise MarketDataValidationError(
                    "RECORD_CONFLICT", "同一业务身份存在非新公告的字段冲突"
                )
        records = tuple(sorted(unique.values(), key=_record_sort_key))
        digest = _candidate_digest(records)
        return records, duplicate_count, digest, tuple(issues)

    # ---- 水位与窗口 ----

    def _watermark(self, kind: str) -> date | None:
        if kind == "TOP10":
            return self._clickhouse.top10_holders_watermark()
        if kind == "TOP10_FLOAT":
            return self._clickhouse.top10_float_holders_watermark()
        return self._clickhouse.holder_count_watermark()

    def _window_days(self, watermark: date | None, target: date) -> tuple[date, ...]:
        """窗口 =（水位, 目标日前一自然日]；表空则水位 = 2024-01-01。

        计划增量窗口最多回看 ``window_lookback_days`` 天：表空/水位陈旧时
        一次性积压（实测空表 600+ 天窗口在 25 分钟截止内取不完，触发
        PROVIDER_DEADLINE）；更深历史由显式回补 Flow 覆盖，天然收敛。
        """
        start = (watermark or BACKFILL_START) + timedelta(days=1)
        earliest = target - timedelta(days=self._window_lookback_days)
        start = max(start, earliest)
        end = target - timedelta(days=1)
        if start > end:
            return ()
        return tuple(
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
        )

    def resolve_backfill_date(
        self, *, data_kind: DataKind, backfill_batch_id: str, target_trade_date: date
    ) -> BackfillDateResolution:
        _validate_backfill_target(target_trade_date, self._timezone)
        if not backfill_batch_id.strip():
            raise ValueError("backfill_batch_id 不能为空")
        return self._repository.resolve_backfill_date(
            data_kind=data_kind.value,
            backfill_batch_id=backfill_batch_id.strip(),
            target_trade_date=target_trade_date,
        )

    # ---- 内部查询（消费契约）----

    def query_shareholder_holdings(
        self,
        *,
        stock_id: str | None = None,
        holder_kind: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._clickhouse.query_shareholder_holdings(
            stock_id=stock_id,
            holder_kind=holder_kind,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )

    def query_shareholder_count(
        self,
        *,
        stock_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._clickhouse.query_shareholder_count(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
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
        return self._repository.list_runs(
            data_kind=data_kind,
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

    def _result(
        self,
        kind: str,
        claim: AttemptClaim,
        counts: SyncCounts,
        error_category: str | None,
        error_summary: str | None,
    ) -> ShareholderDataSyncResult:
        return ShareholderDataSyncResult(
            data_kind=KIND_TO_DATA_KIND[kind].value,
            run_kind=(
                MarketDataRunKind(claim.run_kind).value
                if claim.run_kind
                else MarketDataRunKind.SCHEDULED.value
            ),
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            target_trade_date=claim.target_trade_date,
            status=ShareholderDataSyncStatus.SUCCEEDED,
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

    def _skipped(self, target: date, kind: str) -> ShareholderDataSyncResult:
        return ShareholderDataSyncResult(
            data_kind=KIND_TO_DATA_KIND[kind].value,
            run_kind=MarketDataRunKind.SCHEDULED.value,
            run_id="",
            attempt_id="",
            target_trade_date=target,
            status=ShareholderDataSyncStatus.SKIPPED,
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


def _validate_evidence(evidence: RetrievalEvidence, day: date) -> None:
    """完整性门禁（ED-003）：has_more 收敛 + 请求全部完成 + 无重复页。"""
    if evidence.completed_request_count != evidence.request_count:
        raise MarketDataValidationError("CONTINUATION_INCOMPLETE", "来源请求未全部完成")
    if evidence.repeated_page_detected or not evidence.continuation_exhausted:
        raise MarketDataValidationError(
            "CONTINUATION_INCOMPLETE", f"来源未证明完整覆盖（{day.isoformat()}）"
        )


def _validate_backfill_target(target: date, timezone: ZoneInfo) -> None:
    if target < BACKFILL_START:
        raise ValueError("回补不得早于 2024-01-01")
    if target > datetime.now(timezone).date():
        raise ValueError("回补不得包含未来日期")


def _to_canonical(kind: str, raw: Any, identity: Any) -> Any:
    if kind == "HOLDER_COUNT":
        return ShareholderCount(
            end_date=raw.end_date,
            stock_id=identity.stock_id,
            ann_date=raw.ann_date,
            stock_code=raw.provider_security_id,
            holder_num=raw.holder_num,
        )
    return ShareholderHolding(
        end_date=raw.end_date,
        stock_id=identity.stock_id,
        holder_kind=kind,
        holder_name=raw.holder_name,
        ann_date=raw.ann_date,
        stock_code=raw.provider_security_id,
        hold_amount=raw.hold_amount,
        hold_ratio=raw.hold_ratio,
        hold_float_ratio=raw.hold_float_ratio,
        hold_change=raw.hold_change,
        holder_type=raw.holder_type,
    )


def _holding_identity(record: ShareholderHolding) -> tuple[date, str, str, str]:
    return record.end_date, record.stock_id, record.holder_kind, record.holder_name


def _count_identity(record: ShareholderCount) -> tuple[date, str]:
    return record.end_date, record.stock_id


def _same_holding_values(left: ShareholderHolding, right: ShareholderHolding) -> bool:
    return (
        left.stock_code == right.stock_code
        and left.hold_amount == right.hold_amount
        and left.hold_ratio == right.hold_ratio
        and left.hold_float_ratio == right.hold_float_ratio
        and left.hold_change == right.hold_change
        and left.holder_type == right.holder_type
    )


def _same_count_values(left: ShareholderCount, right: ShareholderCount) -> bool:
    return left.stock_code == right.stock_code and left.holder_num == right.holder_num


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


def _record_sort_key(record: Any) -> tuple[str, str, str]:
    if isinstance(record, ShareholderCount):
        return record.end_date.isoformat(), record.stock_id, record.stock_code
    return record.end_date.isoformat(), record.stock_code, record.holder_name


def _candidate_digest(records: tuple[Any, ...]) -> str:
    canonical_rows = [
        [record.end_date.isoformat(), record.stock_id, record.stock_code]
        for record in records
    ]
    return hashlib.sha256(
        json.dumps(canonical_rows, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
