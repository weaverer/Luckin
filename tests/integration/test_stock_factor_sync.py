"""股票技术面因子同步端到端集成测试（真实 ClickHouse 发布 + 审计幂等演练）。

每测试使用唯一目标交易日与按日期清理，避免共享 ClickHouse 表跨测试污染。
复权修订 vs 稳定字段冲突语义（spec FR-010/ED-009）在本文件用真实
ClickHouse 验证（revision 差异 → updated_count；stable 差异 → RECORD_CONFLICT）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.db import Base
from lucking.models.market_data import MarketDataSyncAttempt
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.repositories.stock_factor_clickhouse import StockFactorClickHouseRepository
from lucking.services.stock_factor import (
    ScheduledStockFactorSyncCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import MemoryStockFactorProvider, make_record

# 2024 年 1 月全部交易日（周末关闭）
_OPEN_DAYS = tuple(date(2024, 1, day) for day in range(1, 32) if date(2024, 1, day).weekday() < 5)
_STOCKS = (
    ("600000.SH", "XSHG", "600000"),
    ("000001.SZ", "XSHE", "000001"),
    ("300750.SZ", "XSHE", "300750"),
    ("830799.BJ", "XBSE", "830799"),
)
_RUN_PREFIX = f"sf-{uuid4().hex[:8]}"


def _stock_id(code: str) -> str:
    return f"{_RUN_PREFIX}-{code}"


_TEST_STOCK_IDS = "(" + ", ".join(f"'{_stock_id(code)}'" for _, _, code in _STOCKS) + ")"


def _unique_target() -> date:
    return _OPEN_DAYS[int(uuid4().hex[:4], 16) % len(_OPEN_DAYS)]


def _build_clickhouse_client(settings: Settings) -> ClickHouseClient:
    return ClickHouseClient(
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


@pytest.fixture
def sync_env() -> tuple[ClickHouseClient, StockFactorService, sessionmaker[Session], date]:
    settings = Settings()
    client = _build_clickhouse_client(settings)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    factory = _seed_factory()
    service = _build_service(factory, client)
    return client, service, factory, _unique_target()


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
        for provider_id, venue, code in _STOCKS:
            stock_id = _stock_id(code)
            session.add(
                StockCurrent(
                    stock_id=stock_id,
                    market_code="CN-S",
                    venue_code=venue,
                    security_code=code,
                    display_name=f"测试股票{code}",
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
                    provider_security_id=provider_id,
                    stock_id=stock_id,
                    last_seen_run_id="seed",
                    last_seen_at=now,
                    created_at=now,
                )
            )
    return factory


def _build_service(factory: sessionmaker[Session], client: ClickHouseClient) -> StockFactorService:
    return StockFactorService(
        MemoryStockFactorProvider(),
        SqlAlchemyMarketDataRepository(factory),
        StockFactorClickHouseRepository(client),
        factory,
    )


def _scheduled(target: date) -> ScheduledStockFactorSyncCommand:
    return ScheduledStockFactorSyncCommand(
        "stock-factor-sync",
        datetime(target.year, target.month, target.day, 9, 0, tzinfo=UTC),
        str(uuid4()),
    )


def _cleanup(client: ClickHouseClient, target: date) -> None:
    client.execute_ddl(
        f"ALTER TABLE {client.database}.stock_factor DELETE "
        f"WHERE stock_id IN {_TEST_STOCK_IDS} SETTINGS mutations_sync = 1"
    )


@pytest.mark.mysql
def test_publish_counts_and_idempotent_claim(
    sync_env: tuple[ClickHouseClient, StockFactorService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = sync_env
    try:
        first = service.sync(_scheduled(target))
        assert first.status is StockFactorSyncStatus.SUCCEEDED
        assert first.added_count == 4
        rows = client.execute(
            f"SELECT stock_id FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}' AND stock_id IN {_TEST_STOCK_IDS}"
        )
        assert len(rows) == 4
        second = service.sync(_scheduled(target))  # 同 run_key（同 slug+时点）
        assert second.status is StockFactorSyncStatus.SUCCEEDED
        assert second.run_id == first.run_id
        assert second.added_count == 0
    finally:
        _cleanup(client, target)


@pytest.mark.mysql
def test_revision_update_vs_stable_conflict(
    sync_env: tuple[ClickHouseClient, StockFactorService, sessionmaker[Session], date],
) -> None:
    """复权字段（close_qfq）更新计 updated；稳定字段（pe_ttm）冲突整批失败。"""
    client, _service, factory, target = sync_env

    class _RevisableProvider(MemoryStockFactorProvider):
        """首次返回原值，第二次复权值变化（模拟除权事件后重算），第三次稳定字段冲突。"""

        call = 0

        def fetch_stock_factors(self, request, *, deadline):  # type: ignore[no-untyped-def]
            self.call += 1
            extra = {}
            if self.call == 2:
                extra = {"close_qfq": Decimal("10.99")}  # 可修订字段变化
            if self.call == 3:
                extra = {"pe_ttm": Decimal("99.00")}  # 稳定字段变化
            return _batch_with(request.target_trade_date, extra)

    provider = _RevisableProvider(codes=("600000.SH",))
    service = StockFactorService(
        provider,
        SqlAlchemyMarketDataRepository(factory),
        StockFactorClickHouseRepository(client),
        factory,
    )
    try:
        first = service.sync(_scheduled(target))
        assert first.status is StockFactorSyncStatus.SUCCEEDED
        assert first.added_count == 1

        # 不同调度时点（同目标交易日）→ 不同 run_key，验证同键修订更新
        second = service.sync(
            ScheduledStockFactorSyncCommand(
                "stock-factor-sync",
                datetime(target.year, target.month, target.day, 10, 0, tzinfo=UTC),
                str(uuid4()),
            )
        )
        assert second.status is StockFactorSyncStatus.SUCCEEDED
        assert second.updated_count == 1  # 复权修订按最新值更新，不视为冲突
        rows = client.execute(
            f"SELECT close_qfq FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}' AND stock_id IN {_TEST_STOCK_IDS}"
        )
        assert str(rows[0]["close_qfq"]).rstrip("0").rstrip(".") == "10.99"

        with pytest.raises(MarketDataValidationError) as excinfo:
            service.sync(
                ScheduledStockFactorSyncCommand(
                    "stock-factor-sync",
                    datetime(target.year, target.month, target.day, 11, 0, tzinfo=UTC),
                    str(uuid4()),
                )
            )
        assert excinfo.value.category == "RECORD_CONFLICT"
        rows = client.execute(
            f"SELECT pe_ttm FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}' AND stock_id IN {_TEST_STOCK_IDS}"
        )
        assert rows[0]["pe_ttm"] is not None  # 既有有效数据未被破坏
        assert str(rows[0]["pe_ttm"]) != "99.00"
    finally:
        _cleanup(client, target)


@pytest.mark.mysql
def test_clickhouse_unreachable_keeps_run_failed_and_data_intact(
    sync_env: tuple[ClickHouseClient, StockFactorService, sessionmaker[Session], date],
) -> None:
    client, _service, factory, target = sync_env

    class _FailingClickHouse(StockFactorClickHouseRepository):
        def publish_batch(self, trade_date, records, updated_at):  # type: ignore[no-untyped-def]
            raise RuntimeError("ClickHouse 不可达")

    service = StockFactorService(
        MemoryStockFactorProvider(),
        SqlAlchemyMarketDataRepository(factory),
        _FailingClickHouse(client),
        factory,
    )
    try:
        with pytest.raises(RuntimeError):
            service.sync(_scheduled(target))
        rows = client.execute(
            f"SELECT count() AS count FROM {client.database}.stock_factor FINAL "
            f"WHERE trade_date = '{target.isoformat()}' AND stock_id IN {_TEST_STOCK_IDS}"
        )
        assert int(rows[0]["count"]) == 0  # 失败不写入任何数据
    finally:
        _cleanup(client, target)


@pytest.mark.mysql
def test_expired_lease_attempt_is_abandoned_and_reopenable(
    sync_env: tuple[ClickHouseClient, StockFactorService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = sync_env
    try:
        with patch(
            "lucking.repositories.stock_factor_clickhouse.ClickHouseClient.insert_rows",
            side_effect=RuntimeError("ClickHouse 不可达"),
        ):
            with pytest.raises(RuntimeError):
                service.sync(_scheduled(target))
        runs = service.list_runs(status="FAILED")
        assert runs
        attempts = service.list_attempts(run_id=runs[0].run_id)
        assert attempts
        first_attempt_id = attempts[0].attempt_id
        # 模拟进程中断：尝试停留在 RUNNING 且租约已过期
        expired_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        with factory.begin() as session:
            session.execute(
                update(MarketDataSyncAttempt)
                .where(MarketDataSyncAttempt.attempt_id == first_attempt_id)
                .values(status="RUNNING", lease_expires_at=expired_at)
            )
        # 再次认领：过期 RUNNING 尝试被置 ABANDONED，新建 attempt 成功
        result = service.sync(_scheduled(target))
        assert result.status is StockFactorSyncStatus.SUCCEEDED
        attempts = service.list_attempts(run_id=result.run_id)
        assert any(attempt.status == "SUCCEEDED" for attempt in attempts)
        assert any(attempt.status == "ABANDONED" for attempt in attempts)
    finally:
        _cleanup(client, target)


def _batch_with(target: date, extra: dict[str, object] | None = None):
    """构造单股票批次的替身返回（供可修订/稳定字段注入测试使用）。"""
    from lucking.models.market_data import RetrievalEvidence
    from lucking.models.stock_factor import ProviderStockFactorBatch

    record = make_record("600000.SH", target, extra=extra)
    return ProviderStockFactorBatch(
        provider_code="memory",
        target_trade_date=target,
        records=(record,),
        evidence=RetrievalEvidence(
            request_count=1,
            completed_request_count=1,
            retry_count=0,
            page_count=1,
            page_limit=10000,
            last_page_count=1,
            received_count=1,
            pagination_enabled=False,
            continuation_exhausted=True,
            repeated_page_detected=False,
        ),
        acquired_at=datetime.now(UTC),
        isolated=(),
    )
