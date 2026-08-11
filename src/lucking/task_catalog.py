"""Explicit catalog of business sync schedules included in the 20:00 snapshot."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    task_key: str
    schedule_slug: str
    display_name: str
    source_domain: str
    cron: str
    timezone: str = "Asia/Shanghai"
    data_kind: str | None = None
    weekdays_only: bool = False
    months: frozenset[int] | None = None
    days_of_month: frozenset[int] | None = None

    def due_on(self, business_date: date) -> bool:
        if self.weekdays_only and business_date.weekday() >= 5:
            return False
        if self.months is not None and business_date.month not in self.months:
            return False
        return self.days_of_month is None or business_date.day in self.days_of_month


SCHEDULED_TASKS = (
    ScheduledTask(
        "trading-calendar-monthly",
        "monthly-current-year",
        "交易日历月度同步",
        "trading-calendar",
        "0 2 1 * *",
        days_of_month=frozenset({1}),
    ),
    ScheduledTask(
        "trading-calendar-year-end",
        "year-end-next-year",
        "交易日历年末同步",
        "trading-calendar",
        "30 2 20 12 *",
        months=frozenset({12}),
        days_of_month=frozenset({20}),
    ),
    ScheduledTask("stock-list", "daily-stock-list", "股票列表同步", "stock-list", "0 9 * * *"),
    ScheduledTask(
        "broker-recommendations",
        "monthly-broker-recommendations",
        "券商金股同步",
        "broker-recommendation",
        "0 12 3,4 * *",
        days_of_month=frozenset({3, 4}),
    ),
    ScheduledTask(
        "adj-factor",
        "adj-factor-sync",
        "复权因子同步",
        "market-data",
        "30 9 * * 1-5",
        data_kind="ADJ_FACTOR",
        weekdays_only=True,
    ),
    ScheduledTask(
        "daily-quote",
        "daily-quote-sync",
        "日线行情同步",
        "market-data",
        "0 17 * * 1-5",
        data_kind="DAILY_QUOTE",
        weekdays_only=True,
    ),
    ScheduledTask(
        "daily-basic",
        "daily-basic-sync",
        "每日基本面同步",
        "market-data",
        "45 17 * * 1-5",
        data_kind="DAILY_BASIC",
        weekdays_only=True,
    ),
    ScheduledTask(
        "weekly-kline",
        "weekly-kline-sync",
        "周 K 线同步",
        "market-data",
        "30 18 * * 1-5",
        data_kind="WEEKLY_KLINE",
        weekdays_only=True,
    ),
    ScheduledTask(
        "monthly-kline",
        "monthly-kline-sync",
        "月 K 线同步",
        "market-data",
        "30 18 * * 1-5",
        data_kind="MONTHLY_KLINE",
        weekdays_only=True,
    ),
    ScheduledTask(
        "index-factor",
        "index-factor-sync",
        "指数技术因子同步",
        "index-factor",
        "0 19 * * 1-5",
        data_kind="INDEX_FACTOR",
        weekdays_only=True,
    ),
    ScheduledTask(
        "stock-factor",
        "stock-factor-sync",
        "股票技术因子同步",
        "stock-factor",
        "0 19 * * 1-5",
        data_kind="STOCK_FACTOR",
        weekdays_only=True,
    ),
    ScheduledTask(
        "top10-holders",
        "top10-holders-sync",
        "前十大股东同步",
        "shareholder-data",
        "0 17 * * 1-5",
        data_kind="TOP10_HOLDERS",
        weekdays_only=True,
    ),
    ScheduledTask(
        "top10-float-holders",
        "top10-floatholders-sync",
        "前十大流通股东同步",
        "shareholder-data",
        "5 17 * * 1-5",
        data_kind="TOP10_FLOAT_HOLDERS",
        weekdays_only=True,
    ),
    ScheduledTask(
        "holder-count",
        "holder-count-sync",
        "股东人数同步",
        "shareholder-data",
        "10 17 * * 1-5",
        data_kind="HOLDER_COUNT",
        weekdays_only=True,
    ),
)


def tasks_due_on(business_date: date) -> tuple[ScheduledTask, ...]:
    return tuple(task for task in SCHEDULED_TASKS if task.due_on(business_date))
