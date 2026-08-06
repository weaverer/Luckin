"""股票技术面因子回补端到端集成测试（真实 ClickHouse + 限流间隔实测）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.db import Base
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.repositories.stock_factor_clickhouse import StockFactorClickHouseRepository
from lucking.services.stock_factor import (
    BackfillStockFactorCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import MemoryStockFactorProvider

_OPEN_DAYS = tuple(
    date(2024, 1, day)
    for day in range(1, 32)
    if date(2024, 1, day).weekday() < 5
)


def _seed_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory.begin() as session:
        for day in range(1, 32):
            session.add(
                TradingCalendar(
                    market_code="CN-S",
                    calendar_date=date(2024, 1, day),
                    is_open=date(2024, 1, day).weekday() < 5,
                    previous_open_date=None,
                    source="tushare",
                    source_market="CN-S",
                    sync_mode="monthly",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.add(
            StockCurrent(
                stock_id="stock-600000",
                market_code="CN-S",
                venue_code="XSHG",
                security_code="600000",
                display_name="测试股票600000",
                currency_code="CNY",
                listing_status="ACTIVE",
                listed_on=date(2000, 1, 1),
                delisted_on=None,
                last_seen_run_id="seed",
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            StockProviderMapping(
                provider_code="memory",
                provider_security_id="600000.SH",
                stock_id="stock-600000",
                last_seen_run_id="seed",
                last_seen_at=now,
                created_at=now,
            )
        )
    return factory


def _build_service(
    factory: sessionmaker[Session], client: ClickHouseClient
) -> StockFactorService:
    return StockFactorService(
        MemoryStockFactorProvider(codes=("600000.SH",)),
        SqlAlchemyMarketDataRepository(factory),
        StockFactorClickHouseRepository(client),
        factory,
    )


def _backfill(
    service: StockFactorService,
    target: date,
    batch_id: str,
) -> object:
    return service.sync(
        BackfillStockFactorCommand(target, batch_id, str(uuid4()))
    )


@pytest.mark.mysql
def test_backfill_overlaps_incremental_with_same_key_semantics() -> None:
    """回补与增量重叠：同键替换无重复；回补逐日独立终态。"""
    settings = Settings()
    client = ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=(
            settings.clickhouse_password.get_secret_value()
            if settings.clickhouse_password is not None
            else None
        ),
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    factory = _seed_factory()
    service = _build_service(factory, client)
    target = _OPEN_DAYS[1]  # 2024-01-02
    batch_id = f"backfill-{uuid4().hex[:8]}"
    try:
        result = _backfill(service, target, batch_id)
        assert result.status is StockFactorSyncStatus.SUCCEEDED
        rows = client.execute(
            f"SELECT stock_id FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}'"
        )
        assert len(rows) == 1  # 同键替换：单行
        # 同 batch 不同日期独立终态
        other = _backfill(service, _OPEN_DAYS[2], batch_id)
        assert other.status is StockFactorSyncStatus.SUCCEEDED
        assert other.run_id != result.run_id
    finally:
        for day in (_OPEN_DAYS[1], _OPEN_DAYS[2]):
            client.execute_ddl(
                f"ALTER TABLE {client.database}.stock_factor DELETE "
                f"WHERE trade_date = '{day.isoformat()}' SETTINGS mutations_sync = 1"
            )


@pytest.mark.mysql
def test_backfill_multiple_days_publish_completely() -> None:
    """多日回补：逐日独立终态且全部落库（节流由 Adapter 层保证，
    T009 已覆盖最小间隔 ≥ 2 秒；真实账户限流行为归 T029 上线门禁）。"""
    settings = Settings()
    client = ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=(
            settings.clickhouse_password.get_secret_value()
            if settings.clickhouse_password is not None
            else None
        ),
    )
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    factory = _seed_factory()
    service = _build_service(factory, client)
    batch_id = f"backfill-{uuid4().hex[:8]}"
    days = (_OPEN_DAYS[1], _OPEN_DAYS[2], _OPEN_DAYS[3])
    try:
        for day in days:
            result = _backfill(service, day, batch_id)
            assert result.status is StockFactorSyncStatus.SUCCEEDED
        for day in days:
            rows = client.execute(
                f"SELECT stock_id FROM {client.database}.stock_factor FINAL "
                f"WHERE trade_date = '{day.isoformat()}'"
            )
            assert len(rows) == 1, f"交易日 {day} 未完整落库"
    finally:
        for day in days:
            client.execute_ddl(
                f"ALTER TABLE {client.database}.stock_factor DELETE "
                f"WHERE trade_date = '{day.isoformat()}' SETTINGS mutations_sync = 1"
            )
