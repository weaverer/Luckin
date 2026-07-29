"""Persistence models for monthly broker recommendations."""

from __future__ import annotations

from datetime import date, datetime
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


class BrokerRecommendationRunKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    BACKFILL = "BACKFILL"


class BrokerRecommendationSyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class BrokerRecommendationAttemptStatus(StrEnum):
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


class BrokerRecommendation(Base):
    __tablename__ = "broker_recommendation"
    __table_args__ = (
        UniqueConstraint("recommendation_id", name="uq_broker_recommendation_uuid"),
        UniqueConstraint(
            "recommendation_month",
            "broker_name",
            "stock_id",
            name="uq_broker_recommendation_business",
        ),
        Index(
            "ix_broker_recommendation_filter",
            "recommendation_month",
            "broker_name",
            "venue_code",
            "security_code",
        ),
        Index("ix_broker_recommendation_month_stock", "recommendation_month", "stock_id"),
        {"comment": "券商月度金股推荐", "mysql_collate": "utf8mb4_bin"},
    )

    id: Mapped[int] = _id()
    recommendation_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="推荐业务UUID"
    )
    recommendation_month: Mapped[date] = mapped_column(
        Date, nullable=False, comment="目标月份第一日"
    )
    broker_name: Mapped[str] = mapped_column(
        String(160), nullable=False, comment="仅规范空白后的券商名称"
    )
    stock_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("stock_current.stock_id"),
        nullable=False,
        comment="项目规范股票业务UUID",
    )
    venue_code: Mapped[str] = mapped_column(
        String(4), nullable=False, comment="规范交易场所代码：XSHG、XSHE或XBSE"
    )
    security_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="推荐时来源明确返回的规范证券代码"
    )
    stock_name: Mapped[str] = mapped_column(
        String(160), nullable=False, comment="推荐时来源明确返回并可更新的股票简称"
    )
    first_seen_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("broker_recommendation_sync_run.run_id"),
        nullable=False,
        comment="首次成功保存的同步运行业务UUID",
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="首次成功保存的UTC时间"
    )
    last_confirmed_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("broker_recommendation_sync_run.run_id"),
        nullable=False,
        comment="最近可信确认的同步运行业务UUID",
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="最近一次可信确认的UTC时间"
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class BrokerRecommendationSyncRun(Base):
    __tablename__ = "broker_recommendation_sync_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_broker_recommendation_run_uuid"),
        UniqueConstraint("run_key", name="uq_broker_recommendation_run_key"),
        CheckConstraint(
            "(run_kind = 'SCHEDULED' AND schedule_slug IS NOT NULL "
            "AND scheduled_for IS NOT NULL AND backfill_batch_id IS NULL) OR "
            "(run_kind = 'BACKFILL' AND schedule_slug IS NULL "
            "AND scheduled_for IS NULL AND backfill_batch_id IS NOT NULL)",
            name="ck_broker_recommendation_run_identity",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_broker_recommendation_run_status",
        ),
        {"comment": "券商金股同步运行"},
    )

    id: Mapped[int] = _id()
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="同步运行业务UUID")
    run_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="规范运行身份的SHA-256摘要"
    )
    run_kind: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="运行类型：计划运行或历史补跑"
    )
    schedule_slug: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="计划运行标识；补跑为空"
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="计划运行原定UTC时点；补跑为空"
    )
    backfill_batch_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="历史补跑批次幂等键；计划运行为空"
    )
    target_month: Mapped[date] = mapped_column(Date, nullable=False, comment="运行目标月份第一日")
    scope_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="月份、市场与契约版本的审计范围摘要"
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
            "broker_recommendation_sync_attempt.attempt_id",
            use_alter=True,
            name="fk_broker_recommendation_run_success_attempt",
        ),
        nullable=True,
        comment="唯一成功执行尝试的业务UUID",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="成功发布的UTC时间"
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class BrokerRecommendationSyncAttempt(Base):
    __tablename__ = "broker_recommendation_sync_attempt"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_broker_recommendation_attempt_uuid"),
        UniqueConstraint("run_id", "attempt_no", name="uq_broker_recommendation_attempt_no"),
        UniqueConstraint("flow_run_id", name="uq_broker_recommendation_attempt_flow"),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','ABANDONED')",
            name="ck_broker_recommendation_attempt_status",
        ),
        {"comment": "券商金股同步执行尝试"},
    )

    id: Mapped[int] = _id()
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="执行尝试业务UUID")
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("broker_recommendation_sync_run.run_id"),
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
        SmallInteger, nullable=False, default=0, comment="已成功取得的页面数，包含空终止页"
    )
    provider_page_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次页面行数上限"
    )
    provider_last_page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="终止页面原始行数"
    )
    received_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="来源行数"
    )
    valid_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="去重后有效候选数"
    )
    added_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="新增推荐数"
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


class BrokerRecommendationSyncIssue(Base):
    __tablename__ = "broker_recommendation_sync_issue"
    __table_args__ = (
        UniqueConstraint("issue_id", name="uq_broker_recommendation_issue_uuid"),
        Index("ix_broker_recommendation_issue_attempt", "attempt_id"),
        {"comment": "券商金股同步质量问题"},
    )

    id: Mapped[int] = _id()
    issue_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="质量问题业务UUID")
    attempt_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("broker_recommendation_sync_attempt.attempt_id"),
        nullable=False,
        comment="所属执行尝试的业务UUID",
    )
    category: Mapped[str] = mapped_column(String(48), nullable=False, comment="统一问题类别")
    provider_security_id_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="可选数据来源股票标识摘要"
    )
    broker_name_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="可选规范券商名称摘要"
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
