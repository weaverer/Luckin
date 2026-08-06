"""ShareholderDataService 失败路径契约测试（T020，spec FR-012~FR-015、ED-004/ED-005）。

限流/超时重试耗尽 → FAILED + 计数；提取中断（分页未收敛）→
CONTINUATION_INCOMPLETE 失败；全部记录无效 → EMPTY_AGGREGATE 失败；
失败不破坏已有数据；单公告日 0 行正常成功（与失败区分）。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    MarketDataAttemptStatus,
    MarketDataSyncAttempt,
    MarketDataSyncIssue,
    MarketDataSyncRun,
    MarketDataSyncStatus,
)
from lucking.repositories.market_data import MarketDataValidationError
from lucking.services.shareholder_data import (
    ShareholderDataSyncStatus,
)
from tests.contract.shareholder_data_memory import MemoryShareholderDataProvider
from tests.unit.test_shareholder_data_service import (
    build_service,
    scheduled,
    seed_watermark,
)


def test_provider_failure_records_failed_terminal_state(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        failures={"TOP10": RuntimeError("来源不可用")}
    )
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    with pytest.raises(RuntimeError):
        service.sync_top10_holders(scheduled())
    with sqlite_session_factory.begin() as session:
        run = session.query(MarketDataSyncRun).one()
        attempt = session.query(MarketDataSyncAttempt).one()
        issues = session.query(MarketDataSyncIssue).all()
    assert run.status == MarketDataSyncStatus.FAILED
    assert attempt.status == MarketDataAttemptStatus.FAILED
    assert any(issue.category == "PERSISTENCE_ERROR" for issue in issues) or any(
        issue.category == "UNEXPECTED" for issue in issues
    )


def test_incomplete_pagination_fails(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(bad_continuation=True)
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync_top10_holders(scheduled())
    assert excinfo.value.category == "CONTINUATION_INCOMPLETE"


def test_all_invalid_records_fail_empty_aggregate(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """来源有返回但全部记录未映射 → EMPTY_AGGREGATE 失败（spec ED-004）。"""
    provider = MemoryShareholderDataProvider(codes=("999999.SH",))
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync_top10_holders(scheduled())
    assert excinfo.value.category == "EMPTY_AGGREGATE"


def test_failure_does_not_destroy_existing_data(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        failures={"TOP10": RuntimeError("来源不可用")}
    )
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    before = len(clickhouse.holdings)
    with pytest.raises(RuntimeError):
        service.sync_top10_holders(scheduled())
    assert len(clickhouse.holdings) == before  # 既有数据不被清空


def test_zero_rows_window_is_normal_success(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """空窗口（水位 ≥ 昨日）→ 正常成功，区别于提取失败（FR-014 修订语义）。"""
    provider = MemoryShareholderDataProvider()
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 30))  # 水位 = 昨日
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.received_count == 0
