"""指数技术因子同步端到端集成测试（真实 ClickHouse 发布 + 审计幂等演练）。

每测试使用唯一目标交易日与按日期清理，避免共享 ClickHouse 表跨测试污染。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.db import Base
from lucking.models.market_data import MarketDataSyncAttempt
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.index_factor_clickhouse import IndexFactorClickHouseRepository
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.services.index_factor import (
    IndexFactorService,
    IndexFactorSyncStatus,
    ScheduledIndexFactorSyncCommand,
)
from tests.contract.index_factor_memory import MemoryIndexFactorProvider

# 2024 年 1 月全部交易日（周末关闭）
_OPEN_DAYS = tuple(
    date(2024, 1, day)
    for day in range(1, 32)
    if date(2024, 1, day).weekday() < 5
)


def _unique_target() -> date:
    return _OPEN_DAYS[int(uuid4().hex[:4], 16) % len(_OPEN_DAYS)]


@pytest.fixture
def sync_env() -> tuple[ClickHouseClient, IndexFactorService, sessionmaker[Session], date]:
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
    return factory


def _build_service(
    factory: sessionmaker[Session], client: ClickHouseClient
) -> IndexFactorService:
    return IndexFactorService(
        MemoryIndexFactorProvider(),
        SqlAlchemyMarketDataRepository(factory),
        IndexFactorIdentityRepository(factory),
        IndexFactorClickHouseRepository(client),
        factory,
    )


def _scheduled_for(target: date) -> datetime:
    """目标日期在北京时区对应 09:00 UTC 的同一自然日。"""
    return datetime(target.year, target.month, target.day, 9, 0, tzinfo=UTC)


def _scheduled(target: date) -> ScheduledIndexFactorSyncCommand:
    return ScheduledIndexFactorSyncCommand(
        "index-factor-sync", _scheduled_for(target), str(uuid4())
    )


def _cleanup(client: ClickHouseClient, table: str, target: date) -> None:
    client.execute_ddl(
        f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
        "SETTINGS mutations_sync = 1"
    )


@pytest.mark.mysql
def test_publish_counts_and_idempotent_claim(
    sync_env: tuple[ClickHouseClient, IndexFactorService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = sync_env
    table = f"{client.database}.index_factor"
    try:
        first = service.sync(_scheduled(target))
        assert first.status is IndexFactorSyncStatus.SUCCEEDED
        assert first.added_count == 4
        rows = client.execute(
            f"SELECT count() AS c FROM {table} FINAL WHERE trade_date = '{target.isoformat()}'"
        )
        assert int(rows[0]["c"]) == 4
        # 重复 scheduled_at：已成功 run 幂等，不重复发布
        second = service.sync(_scheduled(target))
        assert second.status is IndexFactorSyncStatus.SUCCEEDED
        assert second.added_count == 0
        rows = client.execute(
            f"SELECT count() AS c FROM {table} FINAL WHERE trade_date = '{target.isoformat()}'"
        )
        assert int(rows[0]["c"]) == 4
    finally:
        _cleanup(client, table, target)


@pytest.mark.mysql
def test_clickhouse_unreachable_keeps_run_failed_and_retry_converges(
    sync_env: tuple[ClickHouseClient, IndexFactorService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = sync_env
    table = f"{client.database}.index_factor"
    try:
        with patch(
            "lucking.repositories.index_factor_clickhouse.ClickHouseClient.insert_rows",
            side_effect=RuntimeError("ClickHouse 不可达"),
        ):
            with pytest.raises(RuntimeError):
                service.sync(_scheduled(target))
        runs = service.list_runs(status="FAILED")
        assert runs and runs[0].status == "FAILED"
        assert client.execute(
            f"SELECT count() AS c FROM {table} WHERE trade_date = '{target.isoformat()}'"
        )[0]["c"] == 0
        # 恢复后重试收敛（同一 run_key，FAILED 可重开）
        result = service.sync(_scheduled(target))
        assert result.status is IndexFactorSyncStatus.SUCCEEDED
        assert result.added_count == 4
    finally:
        _cleanup(client, table, target)


@pytest.mark.mysql
def test_expired_lease_attempt_is_abandoned_and_reopenable(
    sync_env: tuple[ClickHouseClient, IndexFactorService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = sync_env
    table = f"{client.database}.index_factor"
    try:
        with patch(
            "lucking.repositories.index_factor_clickhouse.ClickHouseClient.insert_rows",
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
        assert result.status is IndexFactorSyncStatus.SUCCEEDED
        attempts = service.list_attempts(run_id=result.run_id)
        assert any(attempt.status == "SUCCEEDED" for attempt in attempts)
        assert any(attempt.status == "ABANDONED" for attempt in attempts)
    finally:
        _cleanup(client, table, target)


def _build_clickhouse_client(settings: Settings) -> ClickHouseClient:
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
