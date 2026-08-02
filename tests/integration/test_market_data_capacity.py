"""容量与回补测试：全市场 5,400 行、连续重复同步、代表性子集回补。"""

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
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.services.market_data import (
    BackfillMarketDataCommand,
    MarketDataService,
    SyncStatus,
)
from tests.contract.market_data_memory import (
    MemoryAdjFactorProvider,
    MemoryDailyQuoteProvider,
    MemoryWeeklyMonthlyKlineProvider,
)

_MARKET_COUNT = 5400
_TRADE_DAYS = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))


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
            stock_id = f"cap-{index:05d}"
            session.add(
                StockCurrent(
                    stock_id=stock_id,
                    market_code="CN-S",
                    venue_code=venue,
                    security_code=security,
                    display_name=f"容量股票{index}",
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
) -> MarketDataService:
    return MarketDataService(
        {
            DataKind.DAILY_QUOTE: MemoryDailyQuoteProvider(),
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
            DataKind.WEEKLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
            DataKind.MONTHLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
        },
        SqlAlchemyMarketDataRepository(sqlite_session_factory),
        MarketDataClickHouseRepository(clickhouse),
        sqlite_session_factory,
    )


def _count(clickhouse: ClickHouseClient, data_kind: DataKind, target: date) -> int:
    return MarketDataClickHouseRepository(clickhouse).count(data_kind, target)


def _backfill(
    service: MarketDataService, data_kind: DataKind, target: date, batch_id: str
) -> object:
    return service.sync(
        BackfillMarketDataCommand(
            data_kind=data_kind,
            target_trade_date=target,
            backfill_batch_id=batch_id,
            flow_run_id=str(uuid4()),
        )
    )


@pytest.mark.mysql
def test_full_market_capacity_and_thirty_repeat_syncs_produce_no_duplicates(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory, clickhouse)
    table = f"{clickhouse.database}.daily_quote"
    target = _TRADE_DAYS[0]
    try:
        first = _backfill(service, DataKind.DAILY_QUOTE, target, "cap-repeat")
        assert first.status is SyncStatus.SUCCEEDED
        assert first.added_count == _MARKET_COUNT
        # 连续 30 次重复同步：同一批次键已成功，不重复发布、不产生重复记录
        for _ in range(30):
            repeated = _backfill(service, DataKind.DAILY_QUOTE, target, "cap-repeat")
            assert repeated.status is SyncStatus.SUCCEEDED
        assert _count(clickhouse, DataKind.DAILY_QUOTE, target) == _MARKET_COUNT
    finally:
        clickhouse.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_representative_trade_date_set_backfill_is_per_day_independent(
    clickhouse: ClickHouseClient,
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory, clickhouse)
    table = f"{clickhouse.database}.daily_quote"
    try:
        for index, day in enumerate(_TRADE_DAYS):
            result = _backfill(service, DataKind.DAILY_QUOTE, day, f"cap-set-{index}")
            assert result.status is SyncStatus.SUCCEEDED
            assert _count(clickhouse, DataKind.DAILY_QUOTE, day) == _MARKET_COUNT
        # 周月线容量独立：周线已写入时月线表仍为空
        weekly = _backfill(service, DataKind.WEEKLY_KLINE, _TRADE_DAYS[2], "cap-week")
        assert weekly.status is SyncStatus.SUCCEEDED
        weekly_period = date(2023, 12, 29)  # 2024-01-04 所在周之前的周五
        assert _count(clickhouse, DataKind.WEEKLY_KLINE, weekly_period) == _MARKET_COUNT
        assert _count(clickhouse, DataKind.MONTHLY_KLINE, weekly_period) == 0
    finally:
        for day in _TRADE_DAYS:
            clickhouse.execute_ddl(
                f"ALTER TABLE {table} DELETE WHERE trade_date = '{day.isoformat()}' "
                "SETTINGS mutations_sync = 1"
            )
        clickhouse.execute_ddl(
            f"ALTER TABLE {clickhouse.database}.weekly_kline "
            f"DELETE WHERE trade_date = '{weekly_period}' SETTINGS mutations_sync = 1"
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
