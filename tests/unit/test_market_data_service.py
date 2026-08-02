"""MarketDataService 计划同步单元测试（sqlite + Memory Provider + 内存 ClickHouse）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import DataKind, MarketDataSyncRun
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.daily_quote_provider import DailyQuoteRequest, ProviderDailyQuoteBatch
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.market_data import (
    BackfillMarketDataCommand,
    MarketDataService,
    ScheduledMarketDataSyncCommand,
    SyncStatus,
)
from tests.contract.market_data_memory import MemoryAdjFactorProvider, MemoryDailyQuoteProvider

_TARGET = date(2026, 7, 27)


class MemoryClickHouse:
    """内存 ClickHouse 替身：记录发布批次并可注入失败。"""

    def __init__(self) -> None:
        self.published: list[tuple[DataKind, date, int]] = []
        self.fail_insert = False

    def publish_batch(
        self,
        data_kind: DataKind,
        trade_date: date,
        records: tuple[object, ...],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        if self.fail_insert:
            raise RuntimeError("ClickHouse 不可达")
        self.published.append((data_kind, trade_date, len(records)))
        return len(records), 0, 0

    def query(self, *args: object, **kwargs: object) -> tuple[dict[str, object], ...]:
        return ()

    def count(self, data_kind: DataKind, trade_date: date) -> int:
        return 0


@pytest.fixture
def seeded_factory(sqlite_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with sqlite_session_factory.begin() as session:
        for index in range(1, 4):
            stock_id = f"stock-{index:04d}"
            session.add(
                StockCurrent(
                    stock_id=stock_id,
                    market_code="CN-S",
                    venue_code="XSHG",
                    security_code=f"{index:06d}",
                    display_name=f"测试股票{index}",
                    currency_code="CNY",
                    listing_status="ACTIVE",
                    listed_on=date(2020, 1, 1),
                    delisted_on=None,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=datetime.now(UTC).replace(tzinfo=None),
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    updated_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            session.add(
                StockProviderMapping(
                    provider_code="memory",
                    provider_security_id=f"{index:06d}.SH",
                    stock_id=stock_id,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=datetime.now(UTC).replace(tzinfo=None),
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
        for day in range(24, 32):
            session.add(
                TradingCalendar(
                    market_code="CN-S",
                    calendar_date=date(2026, 7, day),
                    is_open=day not in (25, 26),  # 7/25-26 周末
                    previous_open_date=None,
                    source="tushare",
                    source_market="CN-S",
                    sync_mode="monthly",
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    updated_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    providers: dict[DataKind, object] | None = None,
    clickhouse: MemoryClickHouse | None = None,
    daily_quote_suspended: frozenset[str] = frozenset(),
) -> MarketDataService:
    repository = SqlAlchemyMarketDataRepository(sqlite_session_factory)
    all_providers = {
        DataKind.DAILY_QUOTE: MemoryDailyQuoteProvider(suspended=daily_quote_suspended),
        DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
    }
    if providers:
        all_providers.update(providers)
    return MarketDataService(
        all_providers,
        repository,
        clickhouse or MemoryClickHouse(),
        sqlite_session_factory,
    )


def _scheduled(data_kind: DataKind = DataKind.DAILY_QUOTE) -> ScheduledMarketDataSyncCommand:
    return ScheduledMarketDataSyncCommand(
        data_kind=data_kind,
        schedule_slug="daily-quote-sync",
        scheduled_for=datetime(2026, 7, 27, 1, 0, tzinfo=UTC),  # 上海时间 09:00
        flow_run_id=str(uuid4()),
    )


def test_scheduled_command_derives_target_trade_date_and_succeeds(
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory)
    result = service.sync(_scheduled())
    assert result.status is SyncStatus.SUCCEEDED
    assert result.target_trade_date == _TARGET
    assert result.received_count == 5400
    assert result.valid_count == 3  # 只有 3 只种子股票可解析身份
    assert result.invalid_count == 5397
    assert result.added_count == 3


def test_non_trade_day_skips_without_calling_provider(
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory)
    command = ScheduledMarketDataSyncCommand(
        data_kind=DataKind.DAILY_QUOTE,
        schedule_slug="daily-quote-sync",
        scheduled_for=datetime(2026, 7, 25, 1, 0, tzinfo=UTC),  # 周六
        flow_run_id=str(uuid4()),
    )
    result = service.sync(command)
    assert result.status is SyncStatus.SKIPPED
    assert result.run_id == ""
    assert result.received_count == 0


def test_same_run_key_repeat_does_not_call_provider_or_publish(
    seeded_factory: sessionmaker[Session],
) -> None:
    clickhouse = MemoryClickHouse()
    service = _build_service(seeded_factory, clickhouse=clickhouse)
    first = service.sync(_scheduled())
    assert first.status is SyncStatus.SUCCEEDED
    second = service.sync(_scheduled())
    assert second.status is SyncStatus.SUCCEEDED
    assert second.run_id == first.run_id
    assert second.attempt_id == first.attempt_id
    assert clickhouse.published == [(DataKind.DAILY_QUOTE, _TARGET, 3)]


def test_incomplete_evidence_fails_and_records_failure(
    seeded_factory: sessionmaker[Session],
) -> None:
    from dataclasses import replace

    class FlakyEvidenceProvider(MemoryDailyQuoteProvider):
        def __init__(self) -> None:
            super().__init__()
            self.fail_first = True

        def fetch_daily_quotes(
            self, request: DailyQuoteRequest, *, deadline: float
        ) -> ProviderDailyQuoteBatch:
            batch = super().fetch_daily_quotes(request, deadline=deadline)
            if self.fail_first:
                self.fail_first = False
                return replace(
                    batch,
                    evidence=replace(
                        batch.evidence,
                        continuation_exhausted=False,
                        last_page_count=6000,
                    ),
                )
            return batch

    service = _build_service(
        seeded_factory,
        providers={
            DataKind.DAILY_QUOTE: FlakyEvidenceProvider(),
        },
    )
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled())
    assert excinfo.value.category == "CONTINUATION_INCOMPLETE"
    # 失败后 run 保持 FAILED；同一计划再次触发复用原 run 新增尝试并成功
    retry = service.sync(_scheduled())
    assert retry.status is SyncStatus.SUCCEEDED
    assert retry.run_id
    with seeded_factory() as session:
        run = session.scalar(
            select(MarketDataSyncRun).where(MarketDataSyncRun.run_id == retry.run_id)
        )
        assert run is not None
        assert run.attempt_count == 2
        assert run.status == "SUCCEEDED"


def test_all_unknown_identity_fails_empty_aggregate(
    seeded_factory: sessionmaker[Session],
) -> None:
    # 移除全部种子身份：所有候选都解析失败
    with seeded_factory.begin() as session:
        session.query(StockProviderMapping).delete()
        session.query(StockCurrent).delete()
    service = _build_service(seeded_factory)
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled())
    assert excinfo.value.category == "EMPTY_AGGREGATE"


def test_backfill_rejects_out_of_range_dates(
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory)
    with pytest.raises(ValueError, match="2024-01-01"):
        service.sync(
            BackfillMarketDataCommand(
                data_kind=DataKind.DAILY_QUOTE,
                target_trade_date=date(2023, 12, 29),
                backfill_batch_id="demo",
                flow_run_id=str(uuid4()),
            )
        )
    with pytest.raises(ValueError, match="未来"):
        service.sync(
            BackfillMarketDataCommand(
                data_kind=DataKind.DAILY_QUOTE,
                target_trade_date=date(2099, 1, 4),
                backfill_batch_id="demo",
                flow_run_id=str(uuid4()),
            )
        )


def test_clickhouse_failure_keeps_run_non_succeeded(
    seeded_factory: sessionmaker[Session],
) -> None:
    clickhouse = MemoryClickHouse()
    clickhouse.fail_insert = True
    service = _build_service(seeded_factory, clickhouse=clickhouse)
    with pytest.raises(RuntimeError):
        service.sync(_scheduled())
    # 重试前先修复，验证同键收敛后可成功
    clickhouse.fail_insert = False
    result = service.sync(_scheduled())
    assert result.status is SyncStatus.SUCCEEDED
    assert result.added_count == 3


def test_us2_kinds_dispatch_weekly_monthly_and_daily_basic(
    seeded_factory: sessionmaker[Session],
) -> None:
    from tests.contract.market_data_memory import (
        MemoryDailyBasicProvider,
        MemoryWeeklyMonthlyKlineProvider,
    )

    clickhouse = MemoryClickHouse()
    service = MarketDataService(
        {
            DataKind.DAILY_QUOTE: MemoryDailyQuoteProvider(),
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
            DataKind.DAILY_BASIC: MemoryDailyBasicProvider(),
            DataKind.WEEKLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
            DataKind.MONTHLY_KLINE: MemoryWeeklyMonthlyKlineProvider(),
        },
        SqlAlchemyMarketDataRepository(seeded_factory),
        clickhouse,
        seeded_factory,
    )
    basic = service.sync(
        ScheduledMarketDataSyncCommand(
            data_kind=DataKind.DAILY_BASIC,
            schedule_slug="daily-basic-sync",
            scheduled_for=datetime(2026, 7, 27, 9, 45, tzinfo=UTC),
            flow_run_id=str(uuid4()),
        )
    )
    weekly = service.sync(
        ScheduledMarketDataSyncCommand(
            data_kind=DataKind.WEEKLY_KLINE,
            schedule_slug="weekly-kline-sync",
            scheduled_for=datetime(2026, 7, 27, 10, 30, tzinfo=UTC),
            flow_run_id=str(uuid4()),
        )
    )
    monthly = service.sync(
        ScheduledMarketDataSyncCommand(
            data_kind=DataKind.MONTHLY_KLINE,
            schedule_slug="monthly-kline-sync",
            scheduled_for=datetime(2026, 7, 27, 10, 30, tzinfo=UTC),
            flow_run_id=str(uuid4()),
        )
    )
    assert basic.status is SyncStatus.SUCCEEDED
    assert weekly.status is SyncStatus.SUCCEEDED
    assert monthly.status is SyncStatus.SUCCEEDED
    published_kinds = {kind for kind, _, _ in clickhouse.published}
    assert published_kinds == {
        DataKind.DAILY_BASIC,
        DataKind.WEEKLY_KLINE,
        DataKind.MONTHLY_KLINE,
    }


def test_adj_factor_kind_writes_separate_run(
    seeded_factory: sessionmaker[Session],
) -> None:
    clickhouse = MemoryClickHouse()
    service = _build_service(seeded_factory, clickhouse=clickhouse)
    result = service.sync(
        ScheduledMarketDataSyncCommand(
            data_kind=DataKind.ADJ_FACTOR,
            schedule_slug="adj-factor-sync",
            scheduled_for=datetime(2026, 7, 27, 1, 0, tzinfo=UTC),
            flow_run_id=str(uuid4()),
        )
    )
    assert result.status is SyncStatus.SUCCEEDED
    assert result.data_kind is DataKind.ADJ_FACTOR
    assert clickhouse.published == [(DataKind.ADJ_FACTOR, _TARGET, 3)]
    # 同一交易日不同数据类形成不同 run：日线再触发仍为新增
    first = service.sync(_scheduled())
    assert first.status is SyncStatus.SUCCEEDED
    assert len(clickhouse.published) == 2
