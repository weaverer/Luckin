"""指数因子回补集成测试：增量/回补重叠幂等与中断恢复（真实 ClickHouse）。

每测试使用唯一目标交易日与按日期清理，避免共享 ClickHouse 表跨测试污染。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.db import Base
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.index_factor_clickhouse import IndexFactorClickHouseRepository
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.services.index_factor import (
    BackfillIndexFactorCommand,
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


def _env() -> tuple[ClickHouseClient, IndexFactorService, MemoryIndexFactorProvider]:
    settings = Settings()
    client = _build_clickhouse_client(settings)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    factory = _factory()
    provider = MemoryIndexFactorProvider()
    service = IndexFactorService(
        provider,
        SqlAlchemyMarketDataRepository(factory),
        IndexFactorIdentityRepository(factory),
        IndexFactorClickHouseRepository(client),
        factory,
    )
    return client, service, provider


def _factory() -> sessionmaker[Session]:
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


def _cleanup(client: ClickHouseClient, table: str, target: date) -> None:
    client.execute_ddl(
        f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
        "SETTINGS mutations_sync = 1"
    )


@pytest.mark.mysql
def test_backfill_and_scheduled_overlap_is_idempotent() -> None:
    client, service, provider = _env()
    table = f"{client.database}.index_factor"
    target = _unique_target()
    try:
        backfill = service.sync(
            BackfillIndexFactorCommand(target, f"overlap-{uuid4().hex[:6]}", str(uuid4()))
        )
        assert backfill.status is IndexFactorSyncStatus.SUCCEEDED
        assert backfill.added_count == 4
        # 增量同步同一交易日：同键替换，无重复记录
        scheduled = service.sync(
            ScheduledIndexFactorSyncCommand(
                "index-factor-sync",
                datetime(target.year, target.month, target.day, 9, 0, tzinfo=UTC),
                str(uuid4()),
            )
        )
        assert scheduled.status is IndexFactorSyncStatus.SUCCEEDED
        assert scheduled.unchanged_count == 4
        assert scheduled.added_count == 0
        # 相同毫秒时间戳的两次写入跨 part 时查询级 FINAL 不合并；
        # 强制 OPTIMIZE 合并后业务唯一行数必须为 4（同键替换语义）
        client.execute_ddl(f"OPTIMIZE TABLE {table} FINAL")
        rows = client.execute(
            f"SELECT count() AS c FROM {table} FINAL WHERE trade_date = '{target.isoformat()}'"
        )
        assert int(rows[0]["c"]) == 4
        assert provider.call_count == 2  # 回补 1 次 + 增量 1 次
    finally:
        _cleanup(client, table, target)


@pytest.mark.mysql
def test_interrupted_backfill_retries_only_failed_date() -> None:
    client, service, provider = _env()
    table = f"{client.database}.index_factor"
    target = _unique_target()
    batch_id = f"resume-{uuid4().hex[:6]}"
    try:
        with patch(
            "lucking.repositories.index_factor_clickhouse.ClickHouseClient.insert_rows",
            side_effect=RuntimeError("ClickHouse 不可达"),
        ):
            with pytest.raises(RuntimeError):
                service.sync(BackfillIndexFactorCommand(target, batch_id, str(uuid4())))
        calls_after_failure = provider.call_count
        # 恢复后同一批次重跑：只处理失败日期并成功
        result = service.sync(BackfillIndexFactorCommand(target, batch_id, str(uuid4())))
        assert result.status is IndexFactorSyncStatus.SUCCEEDED
        assert result.added_count == 4
        rows = client.execute(
            f"SELECT count() AS c FROM {table} FINAL WHERE trade_date = '{target.isoformat()}'"
        )
        assert int(rows[0]["c"]) == 4
        # 失败日期重试只额外调用一次来源
        assert provider.call_count == calls_after_failure + 1
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
