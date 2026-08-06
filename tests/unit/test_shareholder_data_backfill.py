"""ShareholderDataService 回补单元测试（T016，stock-factor-service.md §4 行为 11）。

区间校验拒绝（早于 2024-01-01 / 未来日期）、逐日幂等（resolve 后
SKIP_SUCCEEDED）、已成功日期不重复调用 Provider（替身调用计数断言）、
回补只处理目标日。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import DataKind
from lucking.repositories.market_data import BackfillDateAction
from lucking.services.shareholder_data import (
    BackfillShareholderDataCommand,
    ShareholderDataSyncStatus,
)
from tests.contract.shareholder_data_memory import MemoryShareholderDataProvider
from tests.unit.test_shareholder_data_service import build_service

_TARGET = date(2026, 7, 28)


def backfill_command(
    target: date = _TARGET, batch: str = "batch-1"
) -> BackfillShareholderDataCommand:
    return BackfillShareholderDataCommand(target, batch, str(uuid4()))


def test_backfill_rejects_before_2024(sqlite_session_factory: sessionmaker[Session]) -> None:
    service, _, _ = build_service(sqlite_session_factory)
    with pytest.raises(ValueError):
        service.backfill_top10_holders(backfill_command(date(2023, 12, 31)))


def test_backfill_rejects_future_date(sqlite_session_factory: sessionmaker[Session]) -> None:
    service, _, _ = build_service(sqlite_session_factory)
    with pytest.raises(ValueError):
        service.backfill_holder_count(backfill_command(date(2099, 1, 1)))


def test_backfill_processes_only_target_day(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, _ = build_service(sqlite_session_factory)
    result = service.backfill_top10_holders(backfill_command())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.added_count == 4
    assert provider.requested_dates["TOP10"] == [_TARGET]  # 只处理目标日


def test_backfill_idempotent_skip_succeeded(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, _ = build_service(sqlite_session_factory)
    first = service.backfill_holder_count(backfill_command(batch="batch-idem"))
    assert first.added_count == 4
    calls_after_first = provider.call_counts["HOLDER_COUNT"]
    # 同一批次日再次 resolve → SKIP_SUCCEEDED；重跑 → already_succeeded 不重复调用
    resolution = service.resolve_backfill_date(
        data_kind=DataKind.HOLDER_COUNT,
        backfill_batch_id="batch-idem",
        target_trade_date=_TARGET,
    )
    assert resolution.action is BackfillDateAction.SKIP_SUCCEEDED
    second = service.backfill_holder_count(backfill_command(batch="batch-idem"))
    assert second.status is ShareholderDataSyncStatus.SUCCEEDED
    assert provider.call_counts["HOLDER_COUNT"] == calls_after_first


def test_backfill_failed_day_can_retry(sqlite_session_factory: sessionmaker[Session]) -> None:
    provider = MemoryShareholderDataProvider(
        failures={"HOLDER_COUNT": RuntimeError("来源不可用")}
    )
    service, _, _ = build_service(sqlite_session_factory, provider=provider)
    with pytest.raises(RuntimeError):
        service.backfill_holder_count(backfill_command(batch="batch-retry"))
    provider.failures.clear()  # 修复来源
    result = service.backfill_holder_count(backfill_command(batch="batch-retry"))
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.added_count == 4


def test_quarter_end_expansion_skips_non_quarter_months() -> None:
    """回补日期展开：top10 仅季度末日期；起始月非季度月不得 KeyError（回归）。"""
    from lucking.flows.shareholder_data import _expansion

    # 2024-01-01 ~ 2024-06-30：季度末 3/31 与 6/30（起始月 1 月非季度月）
    days = _expansion("TOP10", date(2024, 1, 1), date(2024, 6, 30))
    assert days == (date(2024, 3, 31), date(2024, 6, 30))
    # 股东人数按日历日逐日展开
    count_days = _expansion("HOLDER_COUNT", date(2024, 1, 1), date(2024, 1, 3))
    assert count_days == (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))
    # 区间起点恰为季度末
    assert _expansion("TOP10", date(2024, 3, 31), date(2024, 3, 31)) == (
        date(2024, 3, 31),
    )
