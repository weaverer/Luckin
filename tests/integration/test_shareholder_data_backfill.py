"""股东数据回补端到端集成测试（sqlite 审计/身份 + 真实 ClickHouse 发布）。

验证：回补单日成功与计数、逐日幂等（同 batch 已成功日期跳过且不重复
调用来源）、失败日期修复后重跑成功。
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
from tests.integration.test_shareholder_data_sync import _build_service, _cleanup

_TARGET = date(2026, 7, 28)


def _command(batch: str, target: date = _TARGET) -> BackfillShareholderDataCommand:
    return BackfillShareholderDataCommand(target, batch, str(uuid4()))


def test_backfill_day_succeeds_and_idempotent(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, clickhouse = _build_service(sqlite_session_factory)
    try:
        first = service.backfill_holder_count(_command("batch-e2e"))
        assert first.status is ShareholderDataSyncStatus.SUCCEEDED
        assert first.added_count == 4
        calls_after_first = provider.call_counts["HOLDER_COUNT"]
        resolution = service.resolve_backfill_date(
            data_kind=DataKind.HOLDER_COUNT,
            backfill_batch_id="batch-e2e",
            target_trade_date=_TARGET,
        )
        assert resolution.action is BackfillDateAction.SKIP_SUCCEEDED
        second = service.backfill_holder_count(_command("batch-e2e"))
        assert second.status is ShareholderDataSyncStatus.SUCCEEDED
        assert provider.call_counts["HOLDER_COUNT"] == calls_after_first
        rows = clickhouse.execute(
            "SELECT count() AS count FROM lucking.shareholder_count FINAL "
            f"WHERE end_date = '{_TARGET.replace(day=28).isoformat()}'"
        )
        assert rows[0]["count"] == 4  # 无重复记录
    finally:
        _cleanup(clickhouse)


def test_backfill_failure_then_retry(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        codes=("600000.SH", "000001.SZ", "300750.SZ", "830799.BJ"),
        failures={"HOLDER_COUNT": RuntimeError("来源不可用")},
    )
    service, _, clickhouse = _build_service(sqlite_session_factory, provider=provider)
    try:
        with pytest.raises(RuntimeError):
            service.backfill_holder_count(_command("batch-retry"))
        provider.failures.clear()
        result = service.backfill_holder_count(_command("batch-retry"))
        assert result.status is ShareholderDataSyncStatus.SUCCEEDED
        assert result.added_count == 4
    finally:
        _cleanup(clickhouse)
