"""Market-data canonical models and MySQL audit persistence models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lucking.db import Base


class DataKind(StrEnum):
    DAILY_QUOTE = "DAILY_QUOTE"
    ADJ_FACTOR = "ADJ_FACTOR"
    DAILY_BASIC = "DAILY_BASIC"
    WEEKLY_KLINE = "WEEKLY_KLINE"
    MONTHLY_KLINE = "MONTHLY_KLINE"


class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    """供应商提取覆盖证据；Service 用它验证完整覆盖门禁。"""

    request_count: int
    completed_request_count: int
    retry_count: int
    page_count: int
    page_limit: int
    last_page_count: int
    received_count: int
    pagination_enabled: bool
    continuation_exhausted: bool
    repeated_page_detected: bool = False


@dataclass(frozen=True, slots=True)
class ProviderInvalidCandidate:
    """Adapter 层隔离的无效候选（类别必须属于统一问题类别）。"""

    category: str
    safe_summary: str
    field_name: str | None = None
    provider_security_id: str | None = None
    venue_code: VenueCode | None = None
    security_code: str | None = None


@dataclass(frozen=True, slots=True)
class DailyQuote:
    """规范化日线行情（未复权），身份已解析为稳定 stock_id。"""

    trade_date: date
    stock_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    change: Decimal
    pct_chg: Decimal
    vol: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AdjFactor:
    """规范化日线复权因子。"""

    trade_date: date
    stock_id: str
    venue_code: VenueCode
    security_code: str
    adj_factor: Decimal


@dataclass(frozen=True, slots=True)
class DailyBasic:
    """规范化每日基本面指标；None 表示来源未返回（亏损空值正常保存）。"""

    trade_date: date
    stock_id: str
    venue_code: VenueCode
    security_code: str
    pe: Decimal | None
    pe_ttm: Decimal | None
    pb: Decimal | None
    ps: Decimal | None
    ps_ttm: Decimal | None
    dv_ratio: Decimal | None
    dv_ttm: Decimal | None
    total_share: Decimal | None
    float_share: Decimal | None
    free_share: Decimal | None
    total_mv: Decimal | None
    circ_mv: Decimal | None
    turnover_rate: Decimal | None
    turnover_rate_f: Decimal | None
    volume_ratio: Decimal | None
    limit_status: int | None


@dataclass(frozen=True, slots=True)
class WeeklyKline:
    """规范化周K线（独立模型，trade_date 为周期最后交易日）。"""

    trade_date: date
    stock_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vol: Decimal
    amount: Decimal
    change: Decimal
    pct_chg: Decimal
    end_date: date | None


@dataclass(frozen=True, slots=True)
class MonthlyKline:
    """规范化月K线（独立模型，trade_date 为周期最后交易日）。"""

    trade_date: date
    stock_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vol: Decimal
    amount: Decimal
    change: Decimal
    pct_chg: Decimal
    end_date: date | None


class MarketDataRunKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    BACKFILL = "BACKFILL"


class MarketDataSyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class MarketDataAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


def _id() -> Mapped[int]:
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )


def _created() -> Mapped[datetime]:
    return mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


def scheduled_run_key(
    data_kind: DataKind,
    slug: str,
    scheduled_at_utc: datetime,
    target_trade_date: date,
) -> str:
    """计划运行 run_key：data_kind + SCHEDULED + slug + 原计划 UTC 时点 + 目标交易日。"""
    if scheduled_at_utc.tzinfo is None:
        raise ValueError("scheduled_at_utc 必须包含时区")
    canonical = "|".join(
        (
            "SCHEDULED",
            data_kind.value,
            slug,
            scheduled_at_utc.astimezone(UTC).isoformat(timespec="microseconds"),
            target_trade_date.isoformat(),
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def backfill_run_key(data_kind: DataKind, backfill_batch_id: str, target_trade_date: date) -> str:
    """回补运行 run_key：data_kind + BACKFILL + 批次键 + 目标交易日。"""
    canonical = f"BACKFILL|{data_kind.value}|{backfill_batch_id}|{target_trade_date.isoformat()}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def scope_fingerprint(data_kind: DataKind, target_trade_date: date) -> str:
    """审计范围摘要：只记录实际处理范围，不参与 run_key。"""
    canonical = f"market-data-v1|CN-S|{data_kind.value}|{target_trade_date.isoformat()}"
    return hashlib.sha256(canonical.encode()).hexdigest()


class MarketDataSyncRun(Base):
    __tablename__ = "market_data_sync_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_market_data_run_uuid"),
        UniqueConstraint("run_key", name="uq_market_data_run_key"),
        Index(
            "ix_market_data_run_filter",
            "data_kind",
            "target_trade_date",
            "status",
        ),
        CheckConstraint(
            "(run_kind = 'SCHEDULED' AND schedule_slug IS NOT NULL "
            "AND scheduled_for IS NOT NULL AND backfill_batch_id IS NULL) OR "
            "(run_kind = 'BACKFILL' AND schedule_slug IS NULL "
            "AND scheduled_for IS NULL AND backfill_batch_id IS NOT NULL)",
            name="ck_market_data_run_identity",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_market_data_run_status",
        ),
        {"comment": "行情数据同步运行"},
    )

    id: Mapped[int] = _id()
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="同步运行业务UUID")
    run_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="规范运行身份的SHA-256摘要"
    )
    data_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="数据类：DAILY_QUOTE、ADJ_FACTOR、DAILY_BASIC、WEEKLY_KLINE或MONTHLY_KLINE",
    )
    run_kind: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="运行类型：计划运行或历史回补"
    )
    schedule_slug: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="计划运行标识；回补为空"
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="计划运行原定UTC时点；回补为空"
    )
    backfill_batch_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="历史回补批次幂等键；计划运行为空"
    )
    target_trade_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="运行目标交易日"
    )
    scope_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="交易日、数据类与契约版本的审计范围摘要"
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="运行状态：待执行、执行中、成功或失败"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已创建执行尝试数"
    )
    successful_attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "market_data_sync_attempt.attempt_id",
            use_alter=True,
            name="fk_market_data_run_success_attempt",
        ),
        nullable=True,
        comment="唯一成功执行尝试的业务UUID",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="成功发布的UTC时间"
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class MarketDataSyncAttempt(Base):
    __tablename__ = "market_data_sync_attempt"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_market_data_attempt_uuid"),
        UniqueConstraint("run_id", "attempt_no", name="uq_market_data_attempt_no"),
        UniqueConstraint("flow_run_id", name="uq_market_data_attempt_flow"),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','ABANDONED')",
            name="ck_market_data_attempt_status",
        ),
        {"comment": "行情数据同步执行尝试"},
    )

    id: Mapped[int] = _id()
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="执行尝试业务UUID")
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("market_data_sync_run.run_id"),
        nullable=False,
        comment="所属同步运行的业务UUID",
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="运行内从1递增")
    flow_run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Prefect工作流运行标识"
    )
    provider_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="本次选中的数据来源代码"
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="尝试状态：执行中、成功、失败或已放弃"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="实际开始的UTC时间"
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="运行租约到期的UTC时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="进入终态的UTC时间"
    )
    provider_request_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="实际数据来源请求次数"
    )
    provider_retry_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="初次调用之外的重试次数"
    )
    provider_page_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="已成功取得的提取批次数，包含空终止批"
    )
    provider_page_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次批次行数上限"
    )
    provider_last_page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="终止批次原始行数"
    )
    received_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="来源行数"
    )
    valid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="去重后有效候选数"
    )
    added_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="新增记录数"
    )
    updated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="业务字段更新数"
    )
    unchanged_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已确认但业务字段未变化数"
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="已解决的完全相同重复数"
    )
    invalid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="无效记录数"
    )
    conflict_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="身份或字段冲突数"
    )
    candidate_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="规范排序候选的SHA-256摘要"
    )
    error_category: Mapped[str | None] = mapped_column(
        String(48), nullable=True, comment="统一安全错误类别"
    )
    error_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="脱敏摘要"
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class MarketDataSyncIssue(Base):
    __tablename__ = "market_data_sync_issue"
    __table_args__ = (
        UniqueConstraint("issue_id", name="uq_market_data_issue_uuid"),
        Index("ix_market_data_issue_attempt", "attempt_id"),
        {"comment": "行情数据同步质量问题"},
    )

    id: Mapped[int] = _id()
    issue_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="质量问题业务UUID")
    attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("market_data_sync_attempt.attempt_id"),
        nullable=False,
        comment="所属执行尝试的业务UUID",
    )
    category: Mapped[str] = mapped_column(String(48), nullable=False, comment="统一问题类别")
    provider_security_id_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="可选数据来源股票标识摘要"
    )
    venue_code: Mapped[str | None] = mapped_column(
        String(4), nullable=True, comment="安全定位用交易场所代码"
    )
    security_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="安全定位代码"
    )
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="规范字段名")
    safe_summary: Mapped[str] = mapped_column(String(500), nullable=False, comment="白名单脱敏摘要")
    payload_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="原始候选摘要，不保存原文"
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
