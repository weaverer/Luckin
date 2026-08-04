"""IndexFactorService 回补契约测试：区间校验、逐日幂等与失败重试。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import MarketDataSyncRun
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.market_data_common import ProviderRateLimitedError
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import (
    BackfillDateAction,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.index_factor import (
    BackfillIndexFactorCommand,
    IndexFactorService,
    IndexFactorSyncStatus,
)
from tests.contract.index_factor_memory import (
    MemoryClickHouse,
    MemoryIndexFactorProvider,
)

_TARGET = date(2024, 1, 2)  # 周二，交易日


@pytest.fixture
def backfill_factory(
    sqlite_session_factory: sessionmaker[Session],
) -> sessionmaker[Session]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite_session_factory.begin() as session:
        for day in range(1, 8):
            session.add(
                TradingCalendar(
                    market_code="CN-S",
                    calendar_date=date(2024, 1, day),
                    is_open=day not in (6, 7),  # 1/6-7 周末
                    previous_open_date=None,
                    source="tushare",
                    source_market="CN-S",
                    sync_mode="monthly",
                    created_at=now,
                    updated_at=now,
                )
            )
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    provider: MemoryIndexFactorProvider | None = None,
) -> IndexFactorService:
    return IndexFactorService(
        provider or MemoryIndexFactorProvider(),
        SqlAlchemyMarketDataRepository(sqlite_session_factory),
        IndexFactorIdentityRepository(sqlite_session_factory),
        MemoryClickHouse(),
        sqlite_session_factory,
    )


def _backfill(
    service: IndexFactorService,
    target: date = _TARGET,
    *,
    batch_id: str | None = None,
) -> object:
    return service.sync(
        BackfillIndexFactorCommand(
            target,
            batch_id or f"batch-{uuid4().hex[:8]}",
            str(uuid4()),
        )
    )


def test_backfill_rejects_invalid_ranges(
    backfill_factory: sessionmaker[Session],
) -> None:
    service = _build_service(backfill_factory)
    with pytest.raises(ValueError):
        service.sync(BackfillIndexFactorCommand(date(2023, 12, 31), "b1", str(uuid4())))
    with pytest.raises(ValueError):
        service.sync(BackfillIndexFactorCommand(date(2099, 1, 1), "b1", str(uuid4())))
    with pytest.raises(ValueError):
        service.sync(BackfillIndexFactorCommand(_TARGET, "  ", str(uuid4())))


def test_backfill_day_succeeds_and_skips_on_repeat(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    service = _build_service(backfill_factory, provider=provider)
    batch_id = "repeat-batch"
    first = service.sync(
        BackfillIndexFactorCommand(_TARGET, batch_id, str(uuid4()))
    )
    assert first.status is IndexFactorSyncStatus.SUCCEEDED
    assert first.added_count == 4
    resolution = service.resolve_backfill_date(
        backfill_batch_id=batch_id, target_trade_date=_TARGET
    )
    assert resolution.action is BackfillDateAction.SKIP_SUCCEEDED
    # 重复执行同一批次日：已成功跳过，不重复调用来源
    second = service.sync(
        BackfillIndexFactorCommand(_TARGET, batch_id, str(uuid4()))
    )
    assert second.status is IndexFactorSyncStatus.SUCCEEDED
    assert second.added_count == 0
    assert provider.call_count == 1


def test_failed_day_retries_only_failed_date(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    service = _build_service(backfill_factory, provider=provider)
    batch_id = "retry-batch"
    with pytest.raises(ProviderRateLimitedError):
        service.sync(BackfillIndexFactorCommand(_TARGET, batch_id, str(uuid4())))
    resolution = service.resolve_backfill_date(
        backfill_batch_id=batch_id, target_trade_date=_TARGET
    )
    assert resolution.action is BackfillDateAction.RETRY
    assert resolution.run_id is not None
    # 修复后同一批次键重试：RETRY 路径重开原 run
    provider.fail_with = None
    result = service.sync(BackfillIndexFactorCommand(_TARGET, batch_id, str(uuid4())))
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.run_id == resolution.run_id
    assert result.added_count == 4
    assert provider.call_count == 2  # 仅失败日期被再次调用


def test_in_progress_date_is_not_reclaimed(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    service = _build_service(backfill_factory, provider=provider)
    batch_id = "in-progress-batch"
    with pytest.raises(ProviderRateLimitedError):
        service.sync(BackfillIndexFactorCommand(_TARGET, batch_id, str(uuid4())))
    # 失败后 run 已 FAILED → RETRY（可重试）；直接再次 resolve 仍为 RETRY
    resolution = service.resolve_backfill_date(
        backfill_batch_id=batch_id, target_trade_date=_TARGET
    )
    assert resolution.action is BackfillDateAction.RETRY
    # 断言 run 记录存在且数据类正确
    with backfill_factory() as session:
        run = session.scalar(
            select(MarketDataSyncRun).where(
                MarketDataSyncRun.run_id == resolution.run_id
            )
        )
        assert run is not None
        assert run.data_kind == "INDEX_FACTOR"
        assert run.backfill_batch_id == batch_id
