"""Persistence models owned by the investment workbench."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from lucking.db import Base


class AppUserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class SummaryStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class TaskExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"
    NOT_RUN = "NOT_RUN"


class NotificationTriggerKind(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL_RETRY = "MANUAL_RETRY"


class NotificationAttemptStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def _id_column() -> Mapped[int]:
    return mapped_column(
        mysql.BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )


def _created_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )


def _updated_at_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=FetchedValue(),
        comment="更新时间",
    )


TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_bin",
}


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_app_user_user_id"),
        UniqueConstraint("username", name="uq_app_user_username"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="ck_app_user_status"),
        {**TABLE_OPTIONS, "comment": "应用用户"},
    )

    id: Mapped[int] = _id_column()
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="用户业务UUID")
    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="规范化登录名")
    display_name: Mapped[str] = mapped_column(String(80), nullable=False, comment="用户显示名称")
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Argon2id密码哈希"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="账号状态")
    password_changed_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=False, comment="最近密码变更UTC时间"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="最近成功登录UTC时间"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class ImportantDate(Base):
    __tablename__ = "important_date"
    __table_args__ = (
        UniqueConstraint("important_date_id", name="uq_important_date_id"),
        UniqueConstraint(
            "user_id", "event_date", "title_key", name="uq_important_date_owner_date_title"
        ),
        Index("ix_important_date_owner_date", "user_id", "event_date"),
        {**TABLE_OPTIONS, "comment": "用户重要日"},
    )

    id: Mapped[int] = _id_column()
    important_date_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="重要日业务UUID"
    )
    user_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("app_user.id"),
        nullable=False,
        comment="所属用户主键ID",
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False, comment="重要日期")
    title: Mapped[str] = mapped_column(String(120), nullable=False, comment="重要日标题")
    title_key: Mapped[str] = mapped_column(String(120), nullable=False, comment="标题规范化唯一键")
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="重要日备注")
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class WatchlistGroup(Base):
    __tablename__ = "watchlist_group"
    __table_args__ = (
        UniqueConstraint("group_id", name="uq_watchlist_group_id"),
        UniqueConstraint("user_id", "name_key", name="uq_watchlist_group_owner_name"),
        Index("ix_watchlist_group_owner_sort", "user_id", "sort_order"),
        {**TABLE_OPTIONS, "comment": "用户自选分组"},
    )

    id: Mapped[int] = _id_column()
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="自选分组业务UUID")
    user_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("app_user.id"),
        nullable=False,
        comment="所属用户主键ID",
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False, comment="分组名称")
    name_key: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="分组名称规范化唯一键"
    )
    notes: Mapped[str] = mapped_column(String(1000), nullable=False, comment="分组备注")
    tags: Mapped[list[str]] = mapped_column(mysql.JSON, nullable=False, comment="分组标签")
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="分组显示顺序"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class WatchlistMember(Base):
    __tablename__ = "watchlist_member"
    __table_args__ = (
        UniqueConstraint("member_id", name="uq_watchlist_member_id"),
        UniqueConstraint("group_id", "stock_id", name="uq_watchlist_member_group_stock"),
        Index("ix_watchlist_member_group_sort", "group_id", "sort_order"),
        {**TABLE_OPTIONS, "comment": "自选分组股票成员"},
    )

    id: Mapped[int] = _id_column()
    member_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="自选成员业务UUID")
    group_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("watchlist_group.id"),
        nullable=False,
        comment="所属自选分组主键ID",
    )
    stock_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stock_current.stock_id"), nullable=False, comment="规范股票业务UUID"
    )
    sort_order: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="股票显示顺序"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class DailyTaskSummary(Base):
    __tablename__ = "daily_task_summary"
    __table_args__ = (
        UniqueConstraint("summary_id", name="uq_daily_task_summary_id"),
        UniqueConstraint("business_date", name="uq_daily_task_summary_business_date"),
        CheckConstraint(
            "status IN ('BUILDING','READY','FAILED')", name="ck_daily_task_summary_status"
        ),
        CheckConstraint(
            "notification_status IN ('PENDING','SENDING','SENT','FAILED')",
            name="ck_daily_task_summary_notification_status",
        ),
        {**TABLE_OPTIONS, "comment": "每日任务汇总"},
    )

    id: Mapped[int] = _id_column()
    summary_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="汇总业务UUID")
    business_date: Mapped[date] = mapped_column(Date, nullable=False, comment="北京时间业务日期")
    scheduled_for: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=False, comment="原定汇总UTC时点"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="汇总状态")
    notification_status: Mapped[str] = mapped_column(String(16), nullable=False, comment="通知状态")
    total_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="纳入统计的任务总数"
    )
    succeeded_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="成功任务数"
    )
    partial_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="部分完成任务数"
    )
    failed_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="失败任务数"
    )
    running_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="运行中任务数"
    )
    unknown_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="未知状态任务数"
    )
    not_run_count: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="应执行但未执行任务数"
    )
    snapshot_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="汇总内容SHA-256摘要"
    )
    generated_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="汇总完成UTC时间"
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="最近成功通知UTC时间"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class DailyTaskSummaryItem(Base):
    __tablename__ = "daily_task_summary_item"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_daily_task_summary_item_id"),
        UniqueConstraint("summary_id", "task_key", name="uq_daily_task_summary_item_task"),
        Index("ix_daily_task_summary_item_status", "summary_id", "status"),
        CheckConstraint(
            "status IN ('SUCCEEDED','PARTIAL','FAILED','RUNNING','UNKNOWN','NOT_RUN')",
            name="ck_daily_task_summary_item_status",
        ),
        {**TABLE_OPTIONS, "comment": "每日任务汇总明细"},
    )

    id: Mapped[int] = _id_column()
    item_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="汇总明细业务UUID")
    summary_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("daily_task_summary.id"),
        nullable=False,
        comment="所属每日汇总主键ID",
    )
    task_key: Mapped[str] = mapped_column(String(128), nullable=False, comment="计划任务规范键")
    schedule_slug: Mapped[str] = mapped_column(String(64), nullable=False, comment="计划调度标识")
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, comment="任务显示名称")
    source_domain: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="运行数据所属领域"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="归一状态")
    source_run_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="来源运行业务标识"
    )
    source_flow_run_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="Prefect Flow Run标识"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="任务开始UTC时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="任务完成UTC时间"
    )
    record_count: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), nullable=True, comment="成功处理记录数"
    )
    error_category: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="安全错误分类"
    )
    error_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="脱敏错误摘要"
    )
    observed_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=False, comment="状态观察UTC时间"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


class DailyTaskNotificationAttempt(Base):
    __tablename__ = "daily_task_notification_attempt"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_daily_task_notification_attempt_id"),
        UniqueConstraint("summary_id", "attempt_no", name="uq_daily_task_notification_attempt_no"),
        Index("ix_daily_task_notification_attempt_summary", "summary_id", "attempt_no"),
        CheckConstraint(
            "trigger_kind IN ('AUTOMATIC','MANUAL_RETRY')",
            name="ck_daily_task_notification_attempt_trigger",
        ),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED')",
            name="ck_daily_task_notification_attempt_status",
        ),
        {**TABLE_OPTIONS, "comment": "每日任务通知尝试"},
    )

    id: Mapped[int] = _id_column()
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="通知尝试业务UUID")
    summary_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True),
        ForeignKey("daily_task_summary.id"),
        nullable=False,
        comment="所属每日汇总主键ID",
    )
    attempt_no: Mapped[int] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=False, comment="汇总内尝试序号"
    )
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False, comment="触发类型")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="尝试状态")
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="通知实现代码")
    response_status: Mapped[int | None] = mapped_column(
        mysql.INTEGER(unsigned=True), nullable=True, comment="外部HTTP响应状态"
    )
    error_category: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="安全错误分类"
    )
    error_summary: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="脱敏错误摘要"
    )
    started_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=False, comment="尝试开始UTC时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        mysql.DATETIME(fsp=6), nullable=True, comment="尝试完成UTC时间"
    )
    created_at: Mapped[datetime] = _created_at_column()
    updated_at: Mapped[datetime] = _updated_at_column()


WORKBENCH_TABLES = {
    model.__tablename__: model.__table__
    for model in (
        AppUser,
        ImportantDate,
        WatchlistGroup,
        WatchlistMember,
        DailyTaskSummary,
        DailyTaskSummaryItem,
        DailyTaskNotificationAttempt,
    )
}
