"""StockFactorService 回补单元测试：区间校验、逐日幂等与失败重试（sqlite）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import BackfillDateAction, SqlAlchemyMarketDataRepository
from lucking.services.stock_factor import (
    BackfillStockFactorCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import MemoryClickHouse, MemoryStockFactorProvider

_TARGET = date(2024, 1, 2)  # 周二，交易日
_STOCKS = (("600000.SH", "XSHG", "600000"),)


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
        provider_id, venue, code = _STOCKS[0]
        session.add(
            StockCurrent(
                stock_id="stock-600000",
                market_code="CN-S",
                venue_code=venue,
                security_code=code,
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
                provider_security_id=provider_id,
                stock_id="stock-600000",
                last_seen_run_id="seed",
                last_seen_at=now,
                created_at=now,
            )
        )
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    provider: MemoryStockFactorProvider | None = None,
) -> StockFactorService:
    return StockFactorService(
        provider or MemoryStockFactorProvider(codes=("600000.SH",)),
        SqlAlchemyMarketDataRepository(sqlite_session_factory),
        MemoryClickHouse(),
        sqlite_session_factory,
    )


def _backfill(
    service: StockFactorService,
    target: date = _TARGET,
    *,
    batch_id: str | None = None,
) -> object:
    return service.sync(
        BackfillStockFactorCommand(
            target,
            batch_id or f"batch-{uuid4().hex[:8]}",
            str(uuid4()),
        )
    )


def test_backfill_rejects_before_2024_start(
    backfill_factory: sessionmaker[Session],
) -> None:
    service = _build_service(backfill_factory)
    with pytest.raises(ValueError, match="2024-01-01"):
        service.sync(BackfillStockFactorCommand(date(2023, 12, 31), "b1", str(uuid4())))


def test_backfill_rejects_future_date(
    backfill_factory: sessionmaker[Session],
) -> None:
    service = _build_service(backfill_factory)
    with pytest.raises(ValueError, match="未来交易日"):
        service.sync(BackfillStockFactorCommand(date(2026, 12, 31), "b1", str(uuid4())))


def test_backfill_flow_rejects_reverse_range() -> None:
    """反向区间（start > end）由 Flow 层区间校验拒绝（FR-018）。"""
    from lucking.flows.stock_factor import _validate_backfill_range

    with pytest.raises(ValueError, match="开始日期不得晚于结束日期"):
        _validate_backfill_range(date(2024, 1, 3), date(2024, 1, 2), "Asia/Shanghai")
    with pytest.raises(ValueError, match="早于 2024-01-01"):
        _validate_backfill_range(date(2023, 12, 31), date(2024, 1, 2), "Asia/Shanghai")


def test_backfill_per_day_independent_terminal_states(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=("600000.SH",))
    service = _build_service(backfill_factory, provider=provider)
    batch_id = f"batch-{uuid4().hex[:8]}"
    first = _backfill(service, date(2024, 1, 2), batch_id=batch_id)
    assert first.status is StockFactorSyncStatus.SUCCEEDED
    second = _backfill(service, date(2024, 1, 3), batch_id=batch_id)
    assert second.status is StockFactorSyncStatus.SUCCEEDED
    assert second.run_id != first.run_id  # 逐日独立终态
    assert provider.call_count == 2


def test_succeeded_date_skipped_without_provider_call(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=("600000.SH",))
    service = _build_service(backfill_factory, provider=provider)
    batch_id = f"batch-{uuid4().hex[:8]}"
    assert _backfill(service, _TARGET, batch_id=batch_id).status is StockFactorSyncStatus.SUCCEEDED
    assert provider.call_count == 1
    # 同日再次提交同 batch_id：SKIP_SUCCEEDED，不重复调用 Provider
    resolution = service.resolve_backfill_date(
        backfill_batch_id=batch_id, target_trade_date=_TARGET
    )
    assert resolution.action is BackfillDateAction.SKIP_SUCCEEDED
    assert provider.call_count == 1


def test_failed_date_retry_only_handles_failed_date(
    backfill_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=("600000.SH",))
    service = _build_service(backfill_factory, provider=provider)
    batch_id = f"batch-{uuid4().hex[:8]}"
    result = _backfill(service, date(2024, 1, 2), batch_id=batch_id)
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    # 1/3 首次失败（注入 Provider 失败），1/4 成功
    provider.fail_with = RuntimeError("瞬时故障")
    with pytest.raises(RuntimeError):
        _backfill(service, date(2024, 1, 3), batch_id=batch_id)
    provider.fail_with = None
    result = _backfill(service, date(2024, 1, 4), batch_id=batch_id)
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    # 失败日期 1/3 可重试（RETRY），已成功日期 1/2、1/4 跳过
    resolution = service.resolve_backfill_date(
        backfill_batch_id=batch_id, target_trade_date=date(2024, 1, 3)
    )
    assert resolution.action is BackfillDateAction.RETRY
    assert (
        service.resolve_backfill_date(
            backfill_batch_id=batch_id, target_trade_date=date(2024, 1, 4)
        ).action
        is BackfillDateAction.SKIP_SUCCEEDED
    )
