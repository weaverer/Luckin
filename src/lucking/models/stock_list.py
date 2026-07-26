"""Current stock list, Provider mapping, run and issue persistence models."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from lucking.db import Base


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class StockCurrent(Base):
    __tablename__ = "stock_current"
    __table_args__ = (
        UniqueConstraint(
            "market_code", "venue_code", "security_code", name="uq_stock_current_identity"
        ),
        CheckConstraint(
            "market_code = 'CN-S'", name="ck_stock_current_market"
        ),
        CheckConstraint(
            "venue_code IN ('XSHG','XSHE','XBSE')", name="ck_stock_current_venue"
        ),
        CheckConstraint(
            "listing_status IN ('ACTIVE','DELISTED','SUSPENDED','PENDING')",
            name="ck_stock_current_status",
        ),
        Index(
            "ix_stock_current_filter",
            "market_code",
            "listing_status",
            "venue_code",
            "security_code",
        ),
    )

    stock_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    market_code: Mapped[str] = mapped_column(String(4), nullable=False)
    venue_code: Mapped[str] = mapped_column(String(4), nullable=False)
    security_code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    listing_status: Mapped[str] = mapped_column(String(16), nullable=False)
    listed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    delisted_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StockProviderMapping(Base):
    __tablename__ = "stock_provider_mapping"
    __table_args__ = (
        UniqueConstraint(
            "provider_code", "stock_id", name="uq_stock_provider_mapping_stock"
        ),
    )

    provider_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_security_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_current.stock_id"), nullable=False
    )
    last_seen_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StockListSyncRun(Base):
    __tablename__ = "stock_list_sync_run"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_stock_list_sync_run_key"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_stock_list_sync_run_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    scope_code: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    flow_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    completed_segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capped_segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class StockListSyncIssue(Base):
    __tablename__ = "stock_list_sync_issue"
    __table_args__ = (
        Index("ix_stock_list_sync_issue_run_attempt", "run_id", "attempt_no"),
    )

    issue_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_list_sync_run.run_id"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_security_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    venue_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    security_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

