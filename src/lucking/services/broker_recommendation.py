"""Provider-neutral monthly broker recommendation application service."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from lucking.models.broker_recommendation import (
    BrokerRecommendationRunKind,
    BrokerRecommendationSyncStatus,
)
from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationProvider,
    BrokerRecommendationRequest,
    ProviderBrokerRecommendation,
    RetrievalEvidence,
)
from lucking.repositories.broker_recommendation import (
    AttemptClaim,
    BackfillMonthResolution,
    BrokerRecommendationItem,
    BrokerRecommendationQuery,
    BrokerRecommendationRepository,
    IdentityCandidate,
    RecommendationWrite,
    SyncCounts,
    SyncIssue,
)


@dataclass(frozen=True, slots=True)
class ScheduledBrokerRecommendationSyncCommand:
    schedule_slug: str
    scheduled_at: datetime
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class BackfillBrokerRecommendationMonthCommand:
    target_month: date
    backfill_batch_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class RetryBrokerRecommendationSyncCommand:
    run_id: str
    flow_run_id: str


type BrokerRecommendationSyncCommand = (
    ScheduledBrokerRecommendationSyncCommand
    | BackfillBrokerRecommendationMonthCommand
    | RetryBrokerRecommendationSyncCommand
)


@dataclass(frozen=True, slots=True)
class BrokerRecommendationSyncResult:
    run_id: str
    run_key: str
    attempt_id: str
    attempt_no: int
    status: BrokerRecommendationSyncStatus
    run_kind: BrokerRecommendationRunKind
    target_month: date
    backfill_batch_id: str | None
    provider_code: str
    provider_request_count: int
    provider_retry_count: int
    provider_page_count: int
    provider_page_limit: int
    provider_last_page_count: int
    received_count: int
    valid_count: int
    added_count: int
    updated_count: int
    unchanged_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int


class BrokerRecommendationValidationError(RuntimeError):
    def __init__(self, category: str, summary: str) -> None:
        self.category = category
        self.summary = summary[:500]
        super().__init__(f"{category}: {self.summary}")


class BrokerRecommendationService:
    def __init__(
        self,
        provider: BrokerRecommendationProvider,
        repository: BrokerRecommendationRepository,
        *,
        timezone: str = "Asia/Shanghai",
        fetch_deadline_seconds: int = 1500,
        page_limit: int = 1000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._timezone = ZoneInfo(timezone)
        self._fetch_deadline_seconds = fetch_deadline_seconds
        self._page_limit = page_limit
        self._monotonic = monotonic

    def sync(self, command: BrokerRecommendationSyncCommand) -> BrokerRecommendationSyncResult:
        claim = self._claim(command)
        if claim.already_succeeded:
            return self._result(claim, SyncCounts())
        counts = SyncCounts(provider_page_limit=self._page_limit)
        quality_issues: tuple[SyncIssue, ...] = ()
        try:
            batch = self._provider.fetch_month(
                BrokerRecommendationRequest(claim.target_month),
                deadline=self._monotonic() + self._fetch_deadline_seconds,
            )
            evidence = batch.evidence
            counts = SyncCounts(
                provider_request_count=evidence.request_count,
                provider_retry_count=evidence.retry_count,
                provider_page_count=evidence.page_count,
                provider_page_limit=evidence.page_limit,
                provider_last_page_count=evidence.last_page_count,
                received_count=evidence.received_count,
            )
            self._validate_batch(claim.target_month, batch.target_month, counts, evidence)
            records, duplicate_count, digest, quality_issues = self._prepare_records(
                claim.target_month, batch.records
            )
            counts = SyncCounts(
                provider_request_count=counts.provider_request_count,
                provider_retry_count=counts.provider_retry_count,
                provider_page_count=counts.provider_page_count,
                provider_page_limit=counts.provider_page_limit,
                provider_last_page_count=counts.provider_last_page_count,
                received_count=counts.received_count,
                valid_count=len(records),
                duplicate_count=duplicate_count,
                invalid_count=len(quality_issues),
                candidate_digest=digest,
            )
            if not records:
                raise BrokerRecommendationValidationError("EMPTY_AGGREGATE", "没有可发布的有效推荐")
            final = self._repository.publish_success(claim, records, counts, quality_issues)
            return self._result(claim, final)
        except Exception as exc:
            category = getattr(exc, "category", "PERSISTENCE_ERROR")
            summary = getattr(exc, "summary", "券商金股同步失败")
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

    def resolve_backfill_month(
        self, *, backfill_batch_id: str, target_month: date
    ) -> BackfillMonthResolution:
        _validate_month(target_month)
        if not backfill_batch_id.strip():
            raise ValueError("backfill_batch_id 不能为空")
        return self._repository.resolve_backfill_month(
            backfill_batch_id=backfill_batch_id.strip(), target_month=target_month
        )

    def list_month(self, query: BrokerRecommendationQuery) -> tuple[BrokerRecommendationItem, ...]:
        broker_name = (
            normalize_broker_name(query.broker_name) if query.broker_name is not None else None
        )
        normalized = BrokerRecommendationQuery(
            query.target_month,
            broker_name,
            query.stock_id,
            query.venue_code,
            query.security_code,
            query.limit,
            query.offset,
        )
        return self._repository.list_month(normalized)

    def _claim(self, command: BrokerRecommendationSyncCommand) -> AttemptClaim:
        if isinstance(command, ScheduledBrokerRecommendationSyncCommand):
            if command.scheduled_at.tzinfo is None:
                raise ValueError("scheduled_at 必须包含时区")
            slug = command.schedule_slug.strip()
            if not slug:
                raise ValueError("schedule_slug 不能为空")
            scheduled_utc = command.scheduled_at.astimezone(UTC)
            target = scheduled_utc.astimezone(self._timezone).date().replace(day=1)
            run_key = scheduled_run_key(slug, scheduled_utc, target)
            return self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=BrokerRecommendationRunKind.SCHEDULED,
                target_month=target,
                scope_fingerprint=scope_fingerprint(target),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                schedule_slug=slug,
                scheduled_for=scheduled_utc.replace(tzinfo=None),
            )
        if isinstance(command, BackfillBrokerRecommendationMonthCommand):
            _validate_month(command.target_month)
            current_month = datetime.now(self._timezone).date().replace(day=1)
            if command.target_month > current_month:
                raise ValueError("历史补跑目标月份不得晚于当前月份")
            batch_id = command.backfill_batch_id.strip()
            if not batch_id:
                raise ValueError("backfill_batch_id 不能为空")
            run_key = backfill_run_key(batch_id, command.target_month)
            return self._repository.claim_run_and_start_attempt(
                run_key=run_key,
                run_kind=BrokerRecommendationRunKind.BACKFILL,
                target_month=command.target_month,
                scope_fingerprint=scope_fingerprint(command.target_month),
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                backfill_batch_id=batch_id,
            )
        if isinstance(command, RetryBrokerRecommendationSyncCommand):
            if not command.run_id.strip():
                raise ValueError("run_id 不能为空")
            return self._repository.claim_run_and_start_attempt(
                run_key="",
                run_kind="",
                target_month=date.min,
                scope_fingerprint="",
                flow_run_id=command.flow_run_id,
                provider_code=self._provider.provider_code,
                page_limit=self._page_limit,
                retry_run_id=command.run_id,
            )
        raise TypeError("不支持的同步命令")

    def _validate_batch(
        self,
        target: date,
        batch_target: date,
        counts: SyncCounts,
        evidence: RetrievalEvidence,
    ) -> None:
        if batch_target != target:
            raise BrokerRecommendationValidationError("MONTH_MISMATCH", "批次月份不匹配")
        if counts.received_count <= 0:
            raise BrokerRecommendationValidationError("EMPTY_AGGREGATE", "来源结果为空")
        completed = evidence.completed_request_count
        requests = evidence.request_count
        if completed != requests:
            raise BrokerRecommendationValidationError(
                "CONTINUATION_INCOMPLETE", "来源请求未全部完成"
            )
        if (
            evidence.repeated_page_detected
            or not evidence.continuation_exhausted
            or not 0 <= counts.provider_last_page_count < counts.provider_page_limit
        ):
            raise BrokerRecommendationValidationError(
                "CONTINUATION_INCOMPLETE", "来源未证明完整覆盖"
            )

    def _prepare_records(
        self,
        target: date,
        candidates: tuple[ProviderBrokerRecommendation, ...],
    ) -> tuple[
        tuple[RecommendationWrite, ...],
        int,
        str,
        tuple[SyncIssue, ...],
    ]:
        unique: dict[tuple[date, str, str], RecommendationWrite] = {}
        duplicate_count = 0
        issues: list[SyncIssue] = []
        for raw in candidates:
            if raw.recommendation_month != target:
                raise BrokerRecommendationValidationError("MONTH_MISMATCH", "推荐记录月份不匹配")
            broker = normalize_broker_name(raw.broker_name)
            stock_name = _nonempty(raw.stock_name, "stock_name", 160)
            provider_security_id = _nonempty(raw.provider_security_id, "provider_security_id", 96)
            security_code = _nonempty(raw.security_code, "security_code", 32)
            identity = self._repository.resolve_stock_identity(
                IdentityCandidate(
                    self._provider.provider_code,
                    provider_security_id,
                    raw.venue_code,
                    security_code,
                )
            )
            if identity is None:
                issues.append(
                    SyncIssue(
                        category="UNKNOWN_STOCK_IDENTITY",
                        safe_summary="该推荐无法解析到既有股票身份，已跳过",
                        provider_security_id_hash=hashlib.sha256(
                            provider_security_id.encode()
                        ).hexdigest(),
                        broker_name_hash=hashlib.sha256(broker.encode()).hexdigest(),
                        venue_code=raw.venue_code.value,
                        security_code=security_code,
                    )
                )
                continue
            item = RecommendationWrite(
                target,
                broker,
                identity.stock_id,
                identity.venue_code,
                identity.security_code,
                stock_name,
            )
            key = (target, broker, identity.stock_id)
            previous = unique.get(key)
            if previous is None:
                unique[key] = item
            elif previous == item:
                duplicate_count += 1
            else:
                raise BrokerRecommendationValidationError(
                    "RECOMMENDATION_CONFLICT", "同一业务键存在字段冲突"
                )
        records = tuple(sorted(unique.values(), key=_record_sort_key))
        digest = hashlib.sha256(
            json.dumps(
                [
                    (
                        row.recommendation_month.isoformat(),
                        row.broker_name,
                        row.stock_id,
                        row.venue_code.value,
                        row.security_code,
                        row.stock_name,
                    )
                    for row in records
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return records, duplicate_count, digest, tuple(issues)

    def _result(self, claim: AttemptClaim, counts: SyncCounts) -> BrokerRecommendationSyncResult:
        return BrokerRecommendationSyncResult(
            claim.run_id,
            claim.run_key,
            claim.attempt_id,
            claim.attempt_no,
            BrokerRecommendationSyncStatus.SUCCEEDED,
            BrokerRecommendationRunKind(claim.run_kind),
            claim.target_month,
            claim.backfill_batch_id,
            self._provider.provider_code,
            counts.provider_request_count,
            counts.provider_retry_count,
            counts.provider_page_count,
            counts.provider_page_limit,
            counts.provider_last_page_count,
            counts.received_count,
            counts.valid_count,
            counts.added_count,
            counts.updated_count,
            counts.unchanged_count,
            counts.duplicate_count,
            counts.invalid_count,
            counts.conflict_count,
        )


def normalize_broker_name(value: str) -> str:
    if not isinstance(value, str):
        raise BrokerRecommendationValidationError("INVALID_FIELD", "券商名称类型非法")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 160:
        raise BrokerRecommendationValidationError("INVALID_FIELD", "券商名称为空或过长")
    return normalized


def scheduled_run_key(slug: str, scheduled_at_utc: datetime, target_month: date) -> str:
    if scheduled_at_utc.tzinfo is None:
        raise ValueError("scheduled_at_utc 必须包含时区")
    canonical = "|".join(
        (
            "SCHEDULED",
            slug,
            scheduled_at_utc.astimezone(UTC).isoformat(timespec="microseconds"),
            target_month.isoformat(),
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def backfill_run_key(backfill_batch_id: str, target_month: date) -> str:
    canonical = f"BACKFILL|{backfill_batch_id}|{target_month.isoformat()}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def scope_fingerprint(target_month: date) -> str:
    return hashlib.sha256(f"broker-recommendation-v1|CN-S|{target_month}".encode()).hexdigest()


def _validate_month(value: date) -> None:
    if value.day != 1:
        raise ValueError("目标月份必须是月首")


def _nonempty(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise BrokerRecommendationValidationError("INVALID_FIELD", f"{field} 为空或非法")
    return value.strip()


def _record_sort_key(row: RecommendationWrite) -> tuple[str, str, str, str]:
    return row.broker_name, row.venue_code.value, row.security_code, row.stock_id
