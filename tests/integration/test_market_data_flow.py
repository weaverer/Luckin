"""US1 集成测试：真实 ClickHouse 全市场写入、幂等与停牌无记录。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.models.market_data import DataKind
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.daily_quote_provider import DailyQuoteRequest, ProviderDailyQuoteBatch
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.services.market_data import (
    BackfillMarketDataCommand,
    MarketDataService,
    ScheduledMarketDataSyncCommand,
    SyncStatus,
)
from tests.contract.market_data_memory import (
    MemoryAdjFactorProvider,
    MemoryDailyBasicProvider,
    MemoryDailyQuoteProvider,
    MemoryWeeklyMonthlyKlineProvider,
)

_TARGET = date(2026, 7, 27)
_MARKET_COUNT = 5400


@pytest.fixture
def clickhouse() -> Iterator[ClickHouseClient]:
    settings = Settings()
    client = _build_client(settings)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    yield client


@pytest.fixture
def seeded_factory(sqlite_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite_session_factory.begin() as session:
        session.add(
            TradingCalendar(
                market_code="CN-S",
                calendar_date=_TARGET,
                is_open=True,
                previous_open_date=None,
                source="tushare",
                source_market="CN-S",
                sync_mode="monthly",
                created_at=now,
                updated_at=now,
            )
        )
        for index in range(_MARKET_COUNT):
            if index < 3000:
                venue, security = "XSHG", f"{index + 1:06d}"
                provider_id = f"{index + 1:06d}.SH"
            elif index < 5000:
                venue, security = "XSHE", f"{index + 1:06d}"
                provider_id = f"{index + 1:06d}.SZ"
            else:
                venue, security = "XBSE", f"{index + 1:06d}"
                provider_id = f"{index + 1:06d}.BJ"
            stock_id = f"it-{index:05d}"
            session.add(
                StockCurrent(
                    stock_id=stock_id,
                    market_code="CN-S",
                    venue_code=venue,
                    security_code=security,
                    display_name=f"测试股票{index}",
                    currency_code="CNY",
                    listing_status="ACTIVE",
                    listed_on=date(2020, 1, 1),
                    delisted_on=None,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                StockProviderMapping(
                    provider_code="memory",
                    provider_security_id=provider_id,
                    stock_id=stock_id,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=now,
                    created_at=now,
                )
            )
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    clickhouse: ClickHouseClient,
    *,
    daily_quote_suspended: frozenset[str] = frozenset(),
    daily_basic_loss_making: frozenset[str] = frozenset(),
) -> MarketDataService:
    repository = SqlAlchemyMarketDataRepository(sqlite_session_factory)
    return MarketDataService(
        {
            DataKind.DAILY_QUOTE: MemoryDailyQuoteProvider(suspended=daily_quote_suspended),
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
            DataKind.DAILY_BASIC: MemoryDailyBasicProvider(loss_making=daily_basic_loss_making),
            DataKind.WEEKLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
            DataKind.MONTHLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
        },
        repository,
        MarketDataClickHouseRepository(clickhouse),
        sqlite_session_factory,
    )


def _count(clickhouse: ClickHouseClient, data_kind: DataKind, target: date) -> int:
    return MarketDataClickHouseRepository(clickhouse).count(data_kind, target)


def _backfill(
    data_kind: DataKind, batch_id: str, target: date = _TARGET
) -> BackfillMarketDataCommand:
    return BackfillMarketDataCommand(
        data_kind=data_kind,
        target_trade_date=target,
        backfill_batch_id=batch_id,
        flow_run_id=str(uuid4()),
    )


@pytest.mark.mysql
def test_full_market_daily_quote_and_adj_factor_publish_idempotently(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory, clickhouse)
    table = f"{clickhouse.database}.daily_quote"
    try:
        first = service.sync(_backfill(DataKind.DAILY_QUOTE, "it-daily-1"))
        assert first.status is SyncStatus.SUCCEEDED
        assert first.added_count == _MARKET_COUNT
        assert _count(clickhouse, DataKind.DAILY_QUOTE, _TARGET) == _MARKET_COUNT
        # 同一批次键重复提交：已成功日期跳过，不重复发布
        repeat = service.sync(_backfill(DataKind.DAILY_QUOTE, "it-daily-1"))
        assert repeat.status is SyncStatus.SUCCEEDED
        assert _count(clickhouse, DataKind.DAILY_QUOTE, _TARGET) == _MARKET_COUNT
        # 新批次键主动刷新：同键替换收敛，不产生重复记录
        refresh = service.sync(_backfill(DataKind.DAILY_QUOTE, "it-daily-2"))
        assert refresh.status is SyncStatus.SUCCEEDED
        assert refresh.unchanged_count == _MARKET_COUNT
        assert _count(clickhouse, DataKind.DAILY_QUOTE, _TARGET) == _MARKET_COUNT
        # 复权因子独立写入 adj_factor 表
        factors = service.sync(_backfill(DataKind.ADJ_FACTOR, "it-adj-1"))
        assert factors.status is SyncStatus.SUCCEEDED
        assert factors.added_count == _MARKET_COUNT
        assert _count(clickhouse, DataKind.ADJ_FACTOR, _TARGET) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )
        clickhouse.execute_ddl(
            f"ALTER TABLE {clickhouse.database}.adj_factor DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_suspended_stocks_produce_no_rows_and_sync_still_succeeds(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(
        seeded_factory,
        clickhouse,
        daily_quote_suspended=frozenset({"000001.SH", "000002.SH"}),
    )
    table = f"{clickhouse.database}.daily_quote"
    try:
        result = service.sync(_backfill(DataKind.DAILY_QUOTE, "it-suspend"))
        assert result.status is SyncStatus.SUCCEEDED
        assert result.valid_count == _MARKET_COUNT - 2
        assert _count(clickhouse, DataKind.DAILY_QUOTE, _TARGET) == _MARKET_COUNT - 2
        rows = service.query(DataKind.DAILY_QUOTE, trade_date=_TARGET, limit=10)
        assert len(rows) == 10
        assert all(row["stock_id"] for row in rows)
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_scheduled_sync_on_marker_trade_day_and_skip_on_weekend(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory, clickhouse)
    table = f"{clickhouse.database}.daily_quote"
    try:
        scheduled = service.sync(
            ScheduledMarketDataSyncCommand(
                data_kind=DataKind.DAILY_QUOTE,
                schedule_slug="daily-quote-sync",
                scheduled_for=datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
                flow_run_id=str(uuid4()),
            )
        )
        assert scheduled.status is SyncStatus.SUCCEEDED
        assert _count(clickhouse, DataKind.DAILY_QUOTE, _TARGET) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_daily_basic_loss_making_nulls_and_kline_tables_stay_independent(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(
        seeded_factory,
        clickhouse,
        daily_basic_loss_making=frozenset({"000001.SH"}),
    )
    daily_basic_table = f"{clickhouse.database}.daily_basic"
    weekly_table = f"{clickhouse.database}.weekly_kline"
    monthly_table = f"{clickhouse.database}.monthly_kline"
    weekly_period = date(2026, 7, 24)  # 2026-07-27 周一所在周之前的周五
    monthly_period = date(2026, 6, 30)  # 上一月末
    try:
        basic = service.sync(_backfill(DataKind.DAILY_BASIC, "it-basic-1"))
        assert basic.status is SyncStatus.SUCCEEDED
        assert basic.added_count == _MARKET_COUNT
        assert _count(clickhouse, DataKind.DAILY_BASIC, _TARGET) == _MARKET_COUNT
        # 亏损公司空值正常保存为 NULL
        rows = service.query(
            DataKind.DAILY_BASIC, trade_date=_TARGET, stock_id="it-00000", limit=1
        )
        assert len(rows) == 1
        assert rows[0]["pe"] is None
        assert rows[0]["turnover_rate"] is not None
        # 周线与月线独立写入各自业务表，互不串扰
        weekly = service.sync(_backfill(DataKind.WEEKLY_KLINE, "it-weekly-1"))
        monthly = service.sync(_backfill(DataKind.MONTHLY_KLINE, "it-monthly-1"))
        assert weekly.status is SyncStatus.SUCCEEDED
        assert monthly.status is SyncStatus.SUCCEEDED
        assert _count(clickhouse, DataKind.WEEKLY_KLINE, weekly_period) == _MARKET_COUNT
        assert _count(clickhouse, DataKind.MONTHLY_KLINE, monthly_period) == _MARKET_COUNT
        weekly_rows = service.query(DataKind.WEEKLY_KLINE, trade_date=weekly_period, limit=1)
        assert weekly_rows[0]["close"] is not None
        assert weekly_rows[0]["vol"] is not None
        # 同一周期重复同步只保留一行（同键替换）
        repeat = service.sync(_backfill(DataKind.WEEKLY_KLINE, "it-weekly-2"))
        assert repeat.status is SyncStatus.SUCCEEDED
        assert repeat.unchanged_count == _MARKET_COUNT
        assert _count(clickhouse, DataKind.WEEKLY_KLINE, weekly_period) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {daily_basic_table} DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )
        clickhouse.execute_ddl(
            f"ALTER TABLE {weekly_table} DELETE WHERE trade_date = '{weekly_period}' "
            "SETTINGS mutations_sync = 1"
        )
        clickhouse.execute_ddl(
            f"ALTER TABLE {monthly_table} DELETE WHERE trade_date = '{monthly_period}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_backfill_flow_range_validation_rejects_before_any_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lucking.flows import market_data as flows

    called: list[object] = []
    monkeypatch.setattr(flows, "_build_service", lambda settings: called.append(settings) or None)
    monkeypatch.setattr(flows, "_load_trade_days", lambda *args, **kwargs: set())
    with pytest.raises(ValueError, match="2024-01-01"):
        flows.backfill_market_data(DataKind.DAILY_QUOTE, date(2023, 12, 29), date(2024, 1, 10), "b")
    with pytest.raises(ValueError, match="不得晚于"):
        flows.backfill_market_data(DataKind.DAILY_QUOTE, date(2024, 1, 10), date(2024, 1, 2), "b")
    with pytest.raises(ValueError, match="未来"):
        flows.backfill_market_data(DataKind.DAILY_QUOTE, date(2024, 1, 2), date(2099, 1, 2), "b")
    assert not called  # 区间整体校验在任何运行创建之前


@pytest.mark.mysql
def test_backfill_flow_expands_by_trade_days_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    from lucking.flows import market_data as flows

    service = _build_service(seeded_factory, clickhouse)
    monkeypatch.setattr(flows, "_build_service", lambda settings: service)
    trade_days = {date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)}
    monkeypatch.setattr(flows, "_load_trade_days", lambda *args, **kwargs: trade_days)
    table = f"{clickhouse.database}.daily_quote"
    try:
        first = flows.backfill_market_data(
            DataKind.DAILY_QUOTE, date(2024, 1, 1), date(2024, 1, 5), "it-bf-1"
        )
        assert first["total_trade_day_count"] == 3
        assert first["succeeded_day_count"] == 3
        for day in trade_days:
            assert _count(clickhouse, DataKind.DAILY_QUOTE, day) == _MARKET_COUNT
        # 相同批次键重复提交：已成功日期全部跳过
        second = flows.backfill_market_data(
            DataKind.DAILY_QUOTE, date(2024, 1, 1), date(2024, 1, 5), "it-bf-1"
        )
        assert second["succeeded_day_count"] == 0
        assert second["skipped_day_count"] == 3
        # 新批次键允许主动刷新同一区间
        third = flows.backfill_market_data(
            DataKind.DAILY_QUOTE, date(2024, 1, 1), date(2024, 1, 5), "it-bf-2"
        )
        assert third["succeeded_day_count"] == 3
        for day in trade_days:
            assert _count(clickhouse, DataKind.DAILY_QUOTE, day) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date >= '2024-01-02' "
            "AND trade_date <= '2024-01-04' SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_backfill_flow_failed_day_retries_same_run(
    monkeypatch: pytest.MonkeyPatch,
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    from lucking.flows import market_data as flows

    class FlakyForDate(MemoryDailyQuoteProvider):
        def __init__(self, fail_date: date) -> None:
            super().__init__()
            self.fail_date = fail_date
            self.failed_once = False

        def fetch_daily_quotes(
            self, request: DailyQuoteRequest, *, deadline: float
        ) -> ProviderDailyQuoteBatch:
            if request.target_trade_date == self.fail_date and not self.failed_once:
                self.failed_once = True
                from lucking.ports.market_data_common import ProviderUnavailableError

                raise ProviderUnavailableError("memory", "演练注入超时")
            return super().fetch_daily_quotes(request, deadline=deadline)

    flaky = FlakyForDate(date(2024, 1, 3))
    repository = SqlAlchemyMarketDataRepository(seeded_factory)
    service = MarketDataService(
        {
            DataKind.DAILY_QUOTE: flaky,
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
        },
        repository,
        MarketDataClickHouseRepository(clickhouse),
        seeded_factory,
    )
    monkeypatch.setattr(flows, "_build_service", lambda settings: service)
    trade_days = {date(2024, 1, 2), date(2024, 1, 3)}
    monkeypatch.setattr(flows, "_load_trade_days", lambda *args, **kwargs: trade_days)
    table = f"{clickhouse.database}.daily_quote"
    try:
        first = flows.backfill_market_data(
            DataKind.DAILY_QUOTE, date(2024, 1, 2), date(2024, 1, 3), "it-bf-flaky"
        )
        assert first["succeeded_day_count"] == 1
        assert first["failed_day_count"] == 1
        assert first["failed_dates"] == ["2024-01-03"]
        assert _count(clickhouse, DataKind.DAILY_QUOTE, date(2024, 1, 2)) == _MARKET_COUNT
        # 重跑：失败日期复用原运行重试成功，成功日期跳过
        second = flows.backfill_market_data(
            DataKind.DAILY_QUOTE, date(2024, 1, 2), date(2024, 1, 3), "it-bf-flaky"
        )
        assert second["succeeded_day_count"] == 1
        assert second["skipped_day_count"] == 1
        assert second["failed_day_count"] == 0
        assert _count(clickhouse, DataKind.DAILY_QUOTE, date(2024, 1, 3)) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date >= '2024-01-02' "
            "AND trade_date <= '2024-01-03' SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_five_data_kinds_share_no_runs_and_schedule_skip_on_non_trade_day(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory, clickhouse)
    try:
        weekend = service.sync(
            ScheduledMarketDataSyncCommand(
                data_kind=DataKind.DAILY_BASIC,
                schedule_slug="daily-basic-sync",
                scheduled_for=datetime(2026, 7, 25, 9, 45, tzinfo=UTC),  # 周六
                flow_run_id=str(uuid4()),
            )
        )
        assert weekend.status is SyncStatus.SKIPPED
        # 五类数据各自独立：同一交易日触发另一数据类不产生重复运行冲突
        result = service.sync(_backfill(DataKind.DAILY_BASIC, "it-basic-2"))
        assert result.status is SyncStatus.SUCCEEDED
        assert _count(clickhouse, DataKind.DAILY_BASIC, _TARGET) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {clickhouse.database}.daily_basic DELETE WHERE trade_date = '{_TARGET}' "
            "SETTINGS mutations_sync = 1"
        )


def _build_client(settings: Settings) -> ClickHouseClient:
    password = (
        settings.clickhouse_password.get_secret_value()
        if settings.clickhouse_password is not None
        else None
    )
    return ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=password,
    )
