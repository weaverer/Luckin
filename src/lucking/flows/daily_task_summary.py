from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.runtime import flow_run

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.integrations.feishu import FeishuNotificationSender
from lucking.integrations.task_readers.broker_recommendation import (
    BrokerRecommendationTaskReader,
)
from lucking.integrations.task_readers.index_factor import index_factor_reader
from lucking.integrations.task_readers.market_data import market_data_reader
from lucking.integrations.task_readers.shareholder_data import shareholder_data_reader
from lucking.integrations.task_readers.stock_factor import stock_factor_reader
from lucking.integrations.task_readers.stock_list import StockListTaskReader
from lucking.integrations.task_readers.trading_calendar import TradingCalendarTaskReader
from lucking.logging import JsonlLogStore
from lucking.ports.task_execution_reader import TaskExecutionReader
from lucking.repositories.workbench.task_summaries import SqlAlchemyTaskSummaryRepository
from lucking.services.daily_task_summary import DailyTaskSummaryService


def scheduled_business_date(scheduled_for: datetime) -> date:
    if scheduled_for.tzinfo is None:
        raise ValueError("scheduled_for 必须包含时区")
    return scheduled_for.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _service(settings: Settings) -> DailyTaskSummaryService:
    if settings.feishu_webhook_url is None:
        raise RuntimeError("FEISHU_WEBHOOK_URL 未配置")
    sessions = create_session_factory(create_database_engine(settings))
    readers: list[TaskExecutionReader] = [
        TradingCalendarTaskReader(sessions),
        StockListTaskReader(sessions),
        market_data_reader(sessions),
        index_factor_reader(sessions),
        stock_factor_reader(sessions),
        shareholder_data_reader(sessions),
        BrokerRecommendationTaskReader(sessions),
    ]
    sender = FeishuNotificationSender(
        settings.feishu_webhook_url.get_secret_value(),
        signing_secret=(
            settings.feishu_signing_secret.get_secret_value()
            if settings.feishu_signing_secret
            else None
        ),
    )
    return DailyTaskSummaryService(SqlAlchemyTaskSummaryRepository(sessions), readers, sender)


@flow(name="daily-task-summary", retries=3, retry_delay_seconds=30)
def daily_task_summary(scheduled_for: datetime | None = None) -> str:
    settings = Settings()
    original_time = scheduled_for or flow_run.scheduled_start_time or datetime.now(UTC)
    business_date = scheduled_business_date(original_time)
    summary = _service(settings).run(business_date, original_time)
    log = JsonlLogStore(settings.trading_calendar_log_dir, filename="daily-task-summary.jsonl")
    flow_id = str(flow_run.id or "unknown")
    attempt = int(flow_run.run_count or 1)
    for execution in summary.snapshot.executions:
        log.write(
            "daily_task_observed",
            flow_run_id=flow_id,
            summary_id=summary.summary_id,
            task_key=execution.task_key,
            business_date=business_date,
            status=execution.status,
            attempt=attempt,
            error_category=execution.error_category,
            error_summary=execution.error_summary,
        )
    log.write(
        "daily_summary_completed",
        flow_run_id=flow_id,
        summary_id=summary.summary_id,
        business_date=business_date,
        status=summary.notification_status,
        attempt=attempt,
    )
    return summary.summary_id


@flow(name="resend-daily-task-summary", retries=3, retry_delay_seconds=30)
def resend_daily_task_summary(summary_id: str) -> str:
    settings = Settings()
    service = _service(settings)
    result = service.notify(summary_id, manual_retry=True)
    if result is None:
        raise RuntimeError("已有通知尝试正在运行")
    JsonlLogStore(settings.trading_calendar_log_dir, filename="daily-task-summary.jsonl").write(
        "daily_summary_resent",
        flow_run_id=str(flow_run.id or "unknown"),
        summary_id=summary_id,
        status=result.disposition,
        attempt=int(flow_run.run_count or 1),
    )
    return summary_id
