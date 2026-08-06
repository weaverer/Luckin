"""Provider-neutral market data application service（交易日判断与同步编排）。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    AdjFactor,
    DailyBasic,
    DailyQuote,
    DataKind,
    MarketDataRunKind,
    MonthlyKline,
    ProviderInvalidCandidate,
    RetrievalEvidence,
    WeeklyKline,
    backfill_run_key,
    scheduled_run_key,
    scope_fingerprint,
)
from lucking.ports.adj_factor_provider import AdjFactorProvider, AdjFactorRequest
from lucking.ports.daily_basic_provider import DailyBasicProvider, DailyBasicRequest
from lucking.ports.daily_quote_provider import DailyQuoteProvider, DailyQuoteRequest
from lucking.ports.weekly_monthly_kline_provider import (
    KlineFreq,
    KlineRequest,
    WeeklyMonthlyKlineProvider,
)
from lucking.repositories.market_data import (
    AttemptClaim,
    AttemptDiagnostics,
    BackfillDateResolution,
    IdentityCandidate,
    IssueDiagnostics,
    MarketDataRepository,
    MarketDataValidationError,
    ResolvedStockIdentity,
    RunDiagnostics,
    SyncCounts,
    SyncIssue,
)
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository

BACKFILL_START = date(2024, 1, 1)

type MarketDataProvider = (
    DailyQuoteProvider | AdjFactorProvider | DailyBasicProvider | WeeklyMonthlyKlineProvider
)


class SyncStatus(StrEnum):
    SKIPPED = "SKIPPED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class ScheduledMarketDataSyncCommand:
    data_kind: DataKind
    schedule_slug: str
    scheduled_for: datetime  # 必须包含时区（原计划时点）
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class BackfillMarketDataCommand:
    data_kind: DataKind
    target_trade_date: date
    backfill_batch_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class RetryMarketDataSyncCommand:
    run_id: str
    flow_run_id: str


type MarketDataSyncCommand = (
    ScheduledMarketDataSyncCommand | BackfillMarketDataCommand | RetryMarketDataSyncCommand
)


@dataclass(frozen=True, slots=True)
class MarketDataSyncResult:
    data_kind: DataKind
    run_kind: MarketDataRunKind
    run_id: str
    attempt_id: str
    target_trade_date: date
    status: SyncStatus
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


class MarketDataService:
    def __init__(
        self,
        providers: Mapping[DataKind, MarketDataProvider],
        repository: MarketDataRepository,
        clickhouse_repository: MarketDataClickHouseRepository,
        session_factory: sessionmaker[Session],
        *,
        timezone: str = "Asia/Shanghai",
        fetch_deadline_seconds: int = 1500,
        page_limit: int = 6000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = dict(providers)
        self._repository = repository
        self._clickhouse = clickhouse_repository
        self._session_factory = session_factory
        self._timezone = ZoneInfo(timezone)
        self._fetch_deadline_seconds = fetch_deadline_seconds
        self._page_limit = page_limit
        self._monotonic = monotonic

    def sync(self, command: MarketDataSyncCommand) -> MarketDataSyncResult:
        if isinstance(command, ScheduledMarketDataSyncCommand):
            if command.scheduled_for.tzinfo is None:
                raise ValueError("scheduled_for 必须包含时区")
            slug = command.schedule_slug.strip()
            if not slug:
                raise ValueError("schedule_slug 不能为空")
            scheduled_utc = command.scheduled_for.astimezone(UTC)
            day = scheduled_utc.astimezone(self._timezone).date()
            if not self.is_trade_day(day):
                return self._skipped(command.data_kind, day)
            # ADJ_FACTOR 09:30 开盘后获取：当日复权因子收盘后才发布，
            # 目标日 = 前一个交易日（spec 005 决策）。
            target = (
                self._previous_trade_day(day)
                if command.data_kind is DataKind.ADJ_FACTOR
                else day
            )
            run_key = scheduled_run_key(command.data_kind, slug, scheduled_utc, target)
            claim = self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=MarketDataRunKind.SCHEDULED.value,
                data_kind=command.data_kind.value,
                target_trade_date=target,
                scope_fingerprint=scope_fingerprint(command.data_kind, target),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider(command.data_kind).provider_code,
                page_limit=self._page_limit,
                schedule_slug=slug,
                scheduled_for=scheduled_utc.replace(tzinfo=None),
            )
        elif isinstance(command, BackfillMarketDataCommand):
            _validate_backfill_target(command.target_trade_date, self._timezone)
            batch_id = command.backfill_batch_id.strip()
            if not batch_id:
                raise ValueError("backfill_batch_id 不能为空")
            run_key = backfill_run_key(command.data_kind, batch_id, command.target_trade_date)
            claim = self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=MarketDataRunKind.BACKFILL.value,
                data_kind=command.data_kind.value,
                target_trade_date=command.target_trade_date,
                scope_fingerprint=scope_fingerprint(command.data_kind, command.target_trade_date),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider(command.data_kind).provider_code,
                page_limit=self._page_limit,
                backfill_batch_id=batch_id,
            )
        elif isinstance(command, RetryMarketDataSyncCommand):
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
                provider_code=self._provider(DataKind.DAILY_QUOTE).provider_code,
                page_limit=self._page_limit,
                retry_run_id=run_id,
            )
        else:
            raise TypeError("不支持的同步命令")
        if claim.already_succeeded:
            return self._result(claim, DataKind(claim.data_kind), SyncCounts(), None, None)
        return self._run_sync(claim)

    def _run_sync(self, claim: AttemptClaim) -> MarketDataSyncResult:
        data_kind = DataKind(claim.data_kind)
        target = claim.target_trade_date
        counts = SyncCounts(provider_page_limit=self._page_limit)
        quality_issues: tuple[SyncIssue, ...] = ()
        try:
            deadline = self._monotonic() + self._fetch_deadline_seconds
            raw_records, evidence, isolated = self._fetch(data_kind, target, deadline)
            counts = SyncCounts(
                provider_request_count=evidence.request_count,
                provider_retry_count=evidence.retry_count,
                provider_page_count=evidence.page_count,
                provider_page_limit=evidence.page_limit,
                provider_last_page_count=evidence.last_page_count,
                received_count=evidence.received_count,
            )
            self._validate_batch(data_kind, target, raw_records, evidence, counts)
            records, duplicate_count, digest, quality_issues = self._prepare_records(
                data_kind, target, raw_records, isolated
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
                data_kind, target, records, datetime.now(UTC)
            )
            final = replace(
                counts, added_count=added, updated_count=updated, unchanged_count=unchanged
            )
            self._repository.publish_success(claim, final, quality_issues)
            return self._result(claim, data_kind, final, None, None)
        except Exception as exc:
            category = getattr(exc, "category", "PERSISTENCE_ERROR")
            summary = getattr(exc, "summary", "行情数据同步失败")
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

    def query(
        self,
        data_kind: DataKind,
        *,
        trade_date: date | None = None,
        stock_id: str | None = None,
        venue_code: str | None = None,
        security_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._clickhouse.query(
            data_kind,
            trade_date=trade_date,
            stock_id=stock_id,
            venue_code=venue_code,
            security_code=security_code,
            limit=limit,
            offset=offset,
        )

    def list_runs(
        self,
        *,
        data_kind: DataKind | None = None,
        target_trade_date: date | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[RunDiagnostics, ...]:
        return self._repository.list_runs(
            data_kind=data_kind.value if data_kind is not None else None,
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

    def _previous_trade_day(self, day: date, *, market_code: str = "CN-S") -> date:
        """返回指定日之前最近的交易日（ADJ_FACTOR 09:30 同步的目标日）。

        日历缺口（如迁移初期仅有部分年份）时抛校验错误，由调用方按失败终态记录。
        """
        calendar = SqlAlchemyTradingCalendarRepository(self._session_factory)
        entries = calendar.list_range(
            market_code, day - timedelta(days=14), day - timedelta(days=1)
        )
        open_days = [entry.calendar_date for entry in entries if entry.is_open]
        if not open_days:
            raise MarketDataValidationError(
                "CALENDAR_GAP", f"未找到 {day.isoformat()} 的前一个交易日"
            )
        return max(open_days)

    def _fetch(
        self,
        data_kind: DataKind,
        target: date,
        deadline: float,
    ) -> tuple[
        tuple[Any, ...],
        RetrievalEvidence,
        tuple[ProviderInvalidCandidate, ...],
    ]:
        if data_kind is DataKind.DAILY_QUOTE:
            provider = self._provider(data_kind)
            assert isinstance(provider, DailyQuoteProvider)
            daily_batch = provider.fetch_daily_quotes(DailyQuoteRequest(target), deadline=deadline)
            return daily_batch.records, daily_batch.evidence, daily_batch.isolated
        if data_kind is DataKind.ADJ_FACTOR:
            provider = self._provider(data_kind)
            assert isinstance(provider, AdjFactorProvider)
            factor_batch = provider.fetch_adj_factors(AdjFactorRequest(target), deadline=deadline)
            return factor_batch.records, factor_batch.evidence, factor_batch.isolated
        if data_kind is DataKind.DAILY_BASIC:
            provider = self._provider(data_kind)
            assert isinstance(provider, DailyBasicProvider)
            basic_batch = provider.fetch_daily_basics(DailyBasicRequest(target), deadline=deadline)
            return basic_batch.records, basic_batch.evidence, basic_batch.isolated
        provider = self._provider(data_kind)
        assert isinstance(provider, WeeklyMonthlyKlineProvider)
        freq = KlineFreq.WEEK if data_kind is DataKind.WEEKLY_KLINE else KlineFreq.MONTH
        kline_batch = provider.fetch_kline(KlineRequest(freq, target), deadline=deadline)
        return kline_batch.records, kline_batch.evidence, kline_batch.isolated

    def _validate_batch(
        self,
        data_kind: DataKind,
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
        if data_kind in (DataKind.WEEKLY_KLINE, DataKind.MONTHLY_KLINE):
            expected = KlineFreq.WEEK if data_kind is DataKind.WEEKLY_KLINE else KlineFreq.MONTH
            if not all(record.freq is expected for record in raw_records):
                raise MarketDataValidationError("PERIOD_MISMATCH", "周期类型与请求不一致")
            if not all(record.trade_date <= target for record in raw_records):
                raise MarketDataValidationError("PERIOD_MISMATCH", "周期最后交易日晚于请求交易日")
        elif not all(record.trade_date == target for record in raw_records):
            raise MarketDataValidationError("TRADE_DATE_MISMATCH", "记录交易日与目标交易日不一致")

    def _prepare_records(
        self,
        data_kind: DataKind,
        target: date,
        raw_records: tuple[Any, ...],
        isolated: tuple[ProviderInvalidCandidate, ...],
    ) -> tuple[tuple[Any, ...], int, str, tuple[SyncIssue, ...]]:
        issues: list[SyncIssue] = [_issue_from_candidate(candidate) for candidate in isolated]
        provider = self._provider(data_kind)
        unique: dict[tuple[date, str], Any] = {}
        duplicate_count = 0
        for raw in raw_records:
            identity = self._repository.resolve_stock_identity(
                IdentityCandidate(
                    provider.provider_code,
                    raw.provider_security_id,
                    raw.venue_code,
                    raw.security_code,
                )
            )
            if identity is None:
                issues.append(
                    SyncIssue(
                        category="UNKNOWN_STOCK_IDENTITY",
                        safe_summary="该记录无法解析到既有股票身份，已跳过",
                        provider_security_id_hash=hashlib.sha256(
                            raw.provider_security_id.encode()
                        ).hexdigest(),
                        venue_code=raw.venue_code.value,
                        security_code=raw.security_code,
                    )
                )
                continue
            canonical = _to_canonical(data_kind, raw, identity)
            key = (canonical.trade_date, canonical.stock_id)
            previous = unique.get(key)
            if previous is None:
                unique[key] = canonical
            elif previous == canonical:
                duplicate_count += 1
            else:
                raise MarketDataValidationError("RECORD_CONFLICT", "同一业务键存在字段冲突")
        records = tuple(sorted(unique.values(), key=_record_sort_key))
        digest = _candidate_digest(records)
        return records, duplicate_count, digest, tuple(issues)

    def _provider(self, data_kind: DataKind) -> MarketDataProvider:
        try:
            return self._providers[data_kind]
        except KeyError as exc:
            raise MarketDataValidationError(
                "PROVIDER_CONFIGURATION", f"数据类 {data_kind.value} 未配置 Provider"
            ) from exc

    def _result(
        self,
        claim: AttemptClaim,
        data_kind: DataKind,
        counts: SyncCounts,
        error_category: str | None,
        error_summary: str | None,
    ) -> MarketDataSyncResult:
        return MarketDataSyncResult(
            data_kind=data_kind,
            run_kind=(
                MarketDataRunKind(claim.run_kind) if claim.run_kind else MarketDataRunKind.SCHEDULED
            ),
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            target_trade_date=claim.target_trade_date,
            status=SyncStatus.SUCCEEDED,
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

    def _skipped(self, data_kind: DataKind, target: date) -> MarketDataSyncResult:
        return MarketDataSyncResult(
            data_kind=data_kind,
            run_kind=MarketDataRunKind.SCHEDULED,
            run_id="",
            attempt_id="",
            target_trade_date=target,
            status=SyncStatus.SKIPPED,
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


def _validate_backfill_target(target: date, timezone: ZoneInfo) -> None:
    if target < BACKFILL_START:
        raise ValueError("回补不得早于 2024-01-01")
    if target > datetime.now(timezone).date():
        raise ValueError("回补不得包含未来交易日")


def _issue_from_candidate(candidate: ProviderInvalidCandidate) -> SyncIssue:
    return SyncIssue(
        category=candidate.category,
        safe_summary=candidate.safe_summary,
        provider_security_id_hash=(
            hashlib.sha256(candidate.provider_security_id.encode()).hexdigest()
            if candidate.provider_security_id is not None
            else None
        ),
        venue_code=candidate.venue_code.value if candidate.venue_code is not None else None,
        security_code=candidate.security_code,
        field_name=candidate.field_name,
    )


def _to_canonical(data_kind: DataKind, raw: Any, identity: ResolvedStockIdentity) -> Any:
    if data_kind is DataKind.DAILY_QUOTE:
        return DailyQuote(
            trade_date=raw.trade_date,
            stock_id=identity.stock_id,
            venue_code=identity.venue_code,
            security_code=identity.security_code,
            open=raw.open,
            high=raw.high,
            low=raw.low,
            close=raw.close,
            pre_close=raw.pre_close,
            change=raw.change,
            pct_chg=raw.pct_chg,
            vol=raw.vol,
            amount=raw.amount,
        )
    if data_kind is DataKind.ADJ_FACTOR:
        return AdjFactor(
            trade_date=raw.trade_date,
            stock_id=identity.stock_id,
            venue_code=identity.venue_code,
            security_code=identity.security_code,
            adj_factor=raw.adj_factor,
        )
    if data_kind is DataKind.DAILY_BASIC:
        return DailyBasic(
            trade_date=raw.trade_date,
            stock_id=identity.stock_id,
            venue_code=identity.venue_code,
            security_code=identity.security_code,
            pe=raw.pe,
            pe_ttm=raw.pe_ttm,
            pb=raw.pb,
            ps=raw.ps,
            ps_ttm=raw.ps_ttm,
            dv_ratio=raw.dv_ratio,
            dv_ttm=raw.dv_ttm,
            total_share=raw.total_share,
            float_share=raw.float_share,
            free_share=raw.free_share,
            total_mv=raw.total_mv,
            circ_mv=raw.circ_mv,
            turnover_rate=raw.turnover_rate,
            turnover_rate_f=raw.turnover_rate_f,
            volume_ratio=raw.volume_ratio,
            limit_status=raw.limit_status,
        )
    model = WeeklyKline if data_kind is DataKind.WEEKLY_KLINE else MonthlyKline
    return model(
        trade_date=raw.trade_date,
        stock_id=identity.stock_id,
        venue_code=identity.venue_code,
        security_code=identity.security_code,
        open=raw.open,
        high=raw.high,
        low=raw.low,
        close=raw.close,
        vol=raw.vol,
        amount=raw.amount,
        change=raw.change,
        pct_chg=raw.pct_chg,
        end_date=raw.end_date,
    )


def _record_sort_key(record: Any) -> tuple[str, str, str]:
    return record.venue_code.value, record.security_code, record.stock_id


def _candidate_digest(records: tuple[Any, ...]) -> str:
    canonical_rows = [
        [
            record.trade_date.isoformat(),
            record.stock_id,
            record.venue_code.value,
            record.security_code,
        ]
        for record in records
    ]
    return hashlib.sha256(
        json.dumps(canonical_rows, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
