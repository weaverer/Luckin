"""StockFactorService 计划同步单元测试（sqlite + Memory Provider + 内存 ClickHouse）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import (
    MarketDataSyncIssue,
    MarketDataSyncRun,
    RetrievalEvidence,
)
from lucking.models.stock_factor import ProviderStockFactorBatch
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.stock_factor import (
    BackfillStockFactorCommand,
    ScheduledStockFactorSyncCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import (
    MemoryClickHouse,
    MemoryStockFactorProvider,
    make_record,
)

STOCKS = (
    ("600000.SH", "XSHG", "600000"),
    ("000001.SZ", "XSHE", "000001"),
    ("300750.SZ", "XSHE", "300750"),
    ("830799.BJ", "XBSE", "830799"),
)


def seeded_factory(sqlite_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite_session_factory.begin() as session:
        for day in range(20, 32):
            session.add(
                TradingCalendar(
                    market_code="CN-S",
                    calendar_date=date(2026, 7, day),
                    is_open=day not in (25, 26),  # 7/25-26 周末
                    previous_open_date=None,
                    source="tushare",
                    source_market="CN-S",
                    sync_mode="monthly",
                    created_at=now,
                    updated_at=now,
                )
            )
        for provider_id, venue, code in STOCKS:
            stock_id = f"stock-{code}"
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
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    provider: MemoryStockFactorProvider | None = None,
    clickhouse: MemoryClickHouse | None = None,
) -> StockFactorService:
    repository = SqlAlchemyMarketDataRepository(sqlite_session_factory)
    return StockFactorService(
        provider or MemoryStockFactorProvider(),
        repository,
        clickhouse or MemoryClickHouse(),
        sqlite_session_factory,
    )


def _scheduled(
    scheduled_for: datetime | None = None,
    *,
    slug: str = "stock-factor-sync",
) -> ScheduledStockFactorSyncCommand:
    return ScheduledStockFactorSyncCommand(
        slug,
        scheduled_for or datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
        str(uuid4()),
    )


def _fixed_batch_provider(records: tuple[object, ...] = ()) -> MemoryStockFactorProvider:
    """返回固定记录集的替身 Provider（用于去重/冲突/修订注入）。"""

    class _Fixed(MemoryStockFactorProvider):
        def fetch_stock_factors(self, request, *, deadline):  # type: ignore[no-untyped-def]
            self.call_count += 1
            self.requested_dates.append(request.target_trade_date)
            return ProviderStockFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=request.target_trade_date,
                records=records,
                evidence=RetrievalEvidence(
                    request_count=1,
                    completed_request_count=1,
                    retry_count=0,
                    page_count=1,
                    page_limit=10000,
                    last_page_count=len(records),
                    received_count=len(records),
                    pagination_enabled=False,
                    continuation_exhausted=True,
                    repeated_page_detected=False,
                ),
                acquired_at=datetime.now(UTC),
                isolated=(),
            )

    return _Fixed(codes=())


def test_non_trading_day_is_skipped(sqlite_session_factory: sessionmaker[Session]) -> None:
    service = _build_service(seeded_factory(sqlite_session_factory))
    result = service.sync(_scheduled(datetime(2026, 7, 25, 9, 0, tzinfo=UTC)))  # 周六
    assert result.status is StockFactorSyncStatus.SKIPPED
    assert result.run_id == ""


def test_unknown_identity_is_isolated_and_rest_published(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    factory = seeded_factory(sqlite_session_factory)
    provider = MemoryStockFactorProvider(
        codes=("600000.SH", "999999.SH")  # 999999.SH 未在 003 主数据注册
    )
    service = _build_service(factory, provider=provider)
    result = service.sync(_scheduled())
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    assert result.valid_count == 1
    assert result.invalid_count == 1
    with factory() as session:
        issue = session.scalar(select(MarketDataSyncIssue))
        assert issue is not None
        assert issue.category == "UNKNOWN_STOCK_IDENTITY"


def test_identical_duplicates_are_deduplicated(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    raw = make_record("600000.SH", date(2026, 7, 27))
    provider = _fixed_batch_provider((raw, raw))
    service = _build_service(seeded_factory(sqlite_session_factory), provider=provider)
    result = service.sync(_scheduled())
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    assert result.duplicate_count == 1
    assert result.valid_count == 1


def test_revision_only_difference_within_batch_keeps_latest(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    earlier = make_record("600000.SH", date(2026, 7, 27), extra={"close_qfq": Decimal("10.0")})
    later = make_record("600000.SH", date(2026, 7, 27), extra={"close_qfq": Decimal("10.5")})
    provider = _fixed_batch_provider((earlier, later))
    service = _build_service(seeded_factory(sqlite_session_factory), provider=provider)
    result = service.sync(_scheduled())
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    assert result.duplicate_count == 0
    assert result.valid_count == 1
    assert not result.conflict_count


def test_stable_field_difference_within_batch_raises_conflict(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    row_a = make_record("600000.SH", date(2026, 7, 27), extra={"pe_ttm": Decimal("8.0")})
    row_b = make_record("600000.SH", date(2026, 7, 27), extra={"pe_ttm": Decimal("9.0")})
    provider = _fixed_batch_provider((row_a, row_b))
    service = _build_service(seeded_factory(sqlite_session_factory), provider=provider)
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled())
    assert excinfo.value.category == "RECORD_CONFLICT"


def test_duplicate_sync_is_idempotent_by_run_key(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    factory = seeded_factory(sqlite_session_factory)
    provider = MemoryStockFactorProvider()
    service = _build_service(factory, provider=provider)
    command = _scheduled()
    first = service.sync(command)
    assert first.status is StockFactorSyncStatus.SUCCEEDED
    # 第二次：新 flow_run_id（同 run_key，模拟下一次调度触发）
    second = service.sync(_scheduled(command.scheduled_for, slug=command.schedule_slug))
    assert second.status is StockFactorSyncStatus.SUCCEEDED
    assert second.run_id == first.run_id  # 同一 run_key 复用权威运行
    assert provider.call_count == 1  # 第二次不重复调用 Provider
    with factory() as session:
        assert session.scalar(func.count(MarketDataSyncRun.run_id)) == 1


def test_backfill_command_rejects_future_date(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory(sqlite_session_factory))
    with pytest.raises(ValueError, match="未来交易日"):
        service.sync(
            BackfillStockFactorCommand(date(2026, 12, 31), "batch-x", str(uuid4()))
        )


def test_empty_aggregate_is_failure(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    factory = seeded_factory(sqlite_session_factory)
    provider = MemoryStockFactorProvider(codes=())
    service = _build_service(factory, provider=provider)
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled())
    assert excinfo.value.category == "EMPTY_AGGREGATE"
    with factory() as session:
        run = session.scalar(select(MarketDataSyncRun))
        assert run is not None
        assert run.status == "FAILED"
