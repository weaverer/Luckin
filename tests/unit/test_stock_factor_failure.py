"""StockFactorService 失败路径单元测试：限流/超时/空响应/触顶/冲突不破坏数据。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import RetrievalEvidence
from lucking.models.stock_factor import ProviderStockFactorBatch
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.market_data_common import ProviderRateLimitedError
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.stock_factor import (
    BackfillStockFactorCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import MemoryClickHouse, MemoryStockFactorProvider

_TARGET = date(2024, 1, 3)
_STOCK = ("600000.SH", "XSHG", "600000")


@pytest.fixture
def failure_factory(
    sqlite_session_factory: sessionmaker[Session],
) -> sessionmaker[Session]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite_session_factory.begin() as session:
        for day in range(1, 8):
            session.add(
                TradingCalendar(
                    market_code="CN-S",
                    calendar_date=date(2024, 1, day),
                    is_open=day not in (6, 7),
                    previous_open_date=None,
                    source="tushare",
                    source_market="CN-S",
                    sync_mode="monthly",
                    created_at=now,
                    updated_at=now,
                )
            )
        provider_id, venue, code = _STOCK
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
    clickhouse: MemoryClickHouse | None = None,
) -> StockFactorService:
    return StockFactorService(
        provider or MemoryStockFactorProvider(codes=("600000.SH",)),
        SqlAlchemyMarketDataRepository(sqlite_session_factory),
        clickhouse or MemoryClickHouse(),
        sqlite_session_factory,
    )


def _backfill(
    service: StockFactorService,
    target: date = _TARGET,
) -> object:
    return service.sync(
        BackfillStockFactorCommand(target, f"batch-{uuid4().hex[:8]}", str(uuid4()))
    )


def test_rate_limited_failure_records_failed_terminal_state(
    failure_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=("600000.SH",))
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    service = _build_service(failure_factory, provider=provider)
    with pytest.raises(ProviderRateLimitedError):
        _backfill(service)
    runs = service.list_runs(target_trade_date=_TARGET)
    assert runs and runs[0].status == "FAILED"
    attempts = service.list_attempts(run_id=runs[0].run_id)
    issues = service.list_issues(attempt_id=attempts[0].attempt_id)
    assert any(issue.category == "PROVIDER_RATE_LIMITED" for issue in issues)


def test_empty_aggregate_is_not_success(
    failure_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=())
    service = _build_service(failure_factory, provider=provider)
    with pytest.raises(MarketDataValidationError) as excinfo:
        _backfill(service)
    assert excinfo.value.category == "EMPTY_AGGREGATE"


def test_capped_evidence_fails_as_continuation_incomplete(
    failure_factory: sessionmaker[Session],
) -> None:
    class CappedProvider(MemoryStockFactorProvider):
        def fetch_stock_factors(self, request, *, deadline):  # type: ignore[no-untyped-def]
            batch = super().fetch_stock_factors(request, deadline=deadline)  # type: ignore[arg-type]
            capped = RetrievalEvidence(
                request_count=1,
                completed_request_count=1,
                retry_count=0,
                page_count=1,
                page_limit=10000,
                last_page_count=10000,
                received_count=10000,
                pagination_enabled=False,
                continuation_exhausted=True,
                repeated_page_detected=False,
            )
            return ProviderStockFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=batch.target_trade_date,
                records=batch.records,
                evidence=capped,
                acquired_at=batch.acquired_at,
            )

    service = _build_service(failure_factory, provider=CappedProvider(codes=("600000.SH",)))
    with pytest.raises(MarketDataValidationError) as excinfo:
        _backfill(service)
    assert excinfo.value.category == "CONTINUATION_INCOMPLETE"


def test_failure_after_success_keeps_existing_data(
    failure_factory: sessionmaker[Session],
) -> None:
    provider = MemoryStockFactorProvider(codes=("600000.SH",))
    clickhouse = MemoryClickHouse()
    service = _build_service(failure_factory, provider=provider, clickhouse=clickhouse)
    first = _backfill(service, date(2024, 1, 3))
    assert first.status is StockFactorSyncStatus.SUCCEEDED
    published_before = len(clickhouse.published)
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    with pytest.raises(ProviderRateLimitedError):
        _backfill(service, date(2024, 1, 4))
    assert len(clickhouse.published) == published_before  # 失败不发布任何批次
    runs = service.list_runs(target_trade_date=date(2024, 1, 3))
    assert runs and runs[0].status == "SUCCEEDED"


# 注：稳定字段冲突 vs 复权修订的既有行语义由 tests/integration/test_stock_factor_sync.py
# （真实 ClickHouse）覆盖；同批内冲突由 tests/unit/test_stock_factor_service.py 覆盖。
