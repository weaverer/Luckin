"""IndexFactorService 计划同步单元测试（sqlite + Memory Provider + 内存 ClickHouse）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.index_factor import IndexCurrent, IndexProviderMapping
from lucking.models.market_data import MarketDataSyncIssue
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.index_factor import (
    IndexFactorService,
    IndexFactorSyncStatus,
    ScheduledIndexFactorSyncCommand,
)
from tests.contract.index_factor_memory import (
    MemoryClickHouse,
    MemoryIndexFactorProvider,
    make_record,
)

_TARGET = date(2026, 7, 27)  # 周一，交易日


@pytest.fixture
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
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    provider: MemoryIndexFactorProvider | None = None,
    clickhouse: MemoryClickHouse | None = None,
) -> IndexFactorService:
    repository = SqlAlchemyMarketDataRepository(sqlite_session_factory)
    return IndexFactorService(
        provider or MemoryIndexFactorProvider(),
        repository,
        IndexFactorIdentityRepository(sqlite_session_factory),
        clickhouse or MemoryClickHouse(),
        sqlite_session_factory,
    )


def _scheduled(
    scheduled_for: datetime | None = None,
    *,
    slug: str = "index-factor-sync",
) -> ScheduledIndexFactorSyncCommand:
    return ScheduledIndexFactorSyncCommand(
        slug,
        scheduled_for or datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
        str(uuid4()),
    )


def test_non_trading_day_is_skipped(seeded_factory: sessionmaker[Session]) -> None:
    service = _build_service(seeded_factory)
    result = service.sync(_scheduled(datetime(2026, 7, 25, 9, 0, tzinfo=UTC)))
    assert result.status is IndexFactorSyncStatus.SKIPPED
    assert result.run_id == ""


def test_trading_day_sync_succeeds_and_registers_identities(
    seeded_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    service = _build_service(seeded_factory, provider=provider)
    result = service.sync(_scheduled())
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.received_count == 4
    assert result.valid_count == 4
    assert result.added_count == 4
    assert provider.call_count == 1
    with seeded_factory() as session:
        registered = set(session.scalars(select(IndexCurrent.index_code)))
        assert registered == set(provider.codes)
        mappings = session.scalar(select(func.count()).select_from(IndexProviderMapping))
        assert mappings == 4


def test_repeat_scheduled_sync_is_idempotent(
    seeded_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    service = _build_service(seeded_factory, provider=provider)
    first = service.sync(_scheduled())
    second = service.sync(_scheduled())
    assert first.status is IndexFactorSyncStatus.SUCCEEDED
    assert second.status is IndexFactorSyncStatus.SUCCEEDED
    assert second.added_count == 0
    assert provider.call_count == 1  # 已成功运行不重复调用来源


def test_unknown_suffix_records_are_skipped_with_issue(
    seeded_factory: sessionmaker[Session],
) -> None:
    class MixedProvider(MemoryIndexFactorProvider):
        def fetch_index_factors(self, request: object, *, deadline: float) -> object:
            batch = super().fetch_index_factors(request, deadline=deadline)  # type: ignore[arg-type]
            from lucking.models.index_factor import ProviderIndexFactorBatch

            return ProviderIndexFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=batch.target_trade_date,
                records=batch.records + (
                    make_record("999999.XX", batch.target_trade_date),
                    make_record("", batch.target_trade_date),
                ),
                evidence=batch.evidence,
                acquired_at=batch.acquired_at,
            )

    service = _build_service(seeded_factory, provider=MixedProvider())
    result = service.sync(_scheduled())
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.valid_count == 4
    assert result.invalid_count == 2
    with seeded_factory() as session:
        issues = list(session.scalars(select(MarketDataSyncIssue)))
        assert {issue.category for issue in issues} == {"UNKNOWN_INDEX_IDENTITY"}


def test_all_invalid_records_fail_day(seeded_factory: sessionmaker[Session]) -> None:
    class AllInvalidProvider(MemoryIndexFactorProvider):
        def __init__(self) -> None:
            super().__init__(codes=("999999.XX",))

    service = _build_service(seeded_factory, provider=AllInvalidProvider())
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled())
    assert excinfo.value.category == "EMPTY_AGGREGATE"


def test_duplicates_are_deduped_and_conflicts_fail_batch(
    seeded_factory: sessionmaker[Session],
) -> None:
    class DuplicateProvider(MemoryIndexFactorProvider):
        def fetch_index_factors(self, request: object, *, deadline: float) -> object:
            batch = super().fetch_index_factors(request, deadline=deadline)  # type: ignore[arg-type]
            from lucking.models.index_factor import ProviderIndexFactorBatch

            return ProviderIndexFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=batch.target_trade_date,
                records=batch.records[:1] + batch.records[:1],
                evidence=batch.evidence,
                acquired_at=batch.acquired_at,
            )

    service = _build_service(seeded_factory, provider=DuplicateProvider())
    result = service.sync(_scheduled())
    assert result.valid_count == 1
    assert result.duplicate_count == 1

    class ConflictProvider(MemoryIndexFactorProvider):
        def fetch_index_factors(self, request: object, *, deadline: float) -> object:
            batch = super().fetch_index_factors(request, deadline=deadline)  # type: ignore[arg-type]
            from lucking.models.index_factor import ProviderIndexFactorBatch

            conflicting = make_record(
                batch.records[0].provider_security_id,
                batch.target_trade_date,
                close=Decimal("1234.0000"),
            )
            return ProviderIndexFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=batch.target_trade_date,
                records=batch.records[:1] + (conflicting,),
                evidence=batch.evidence,
                acquired_at=batch.acquired_at,
            )

    service = _build_service(seeded_factory, provider=ConflictProvider())
    # 不同 scheduled_at（run_key 不同），避免命中前半段已成功运行
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.sync(_scheduled(datetime(2026, 7, 27, 10, 0, tzinfo=UTC)))
    assert excinfo.value.category == "RECORD_CONFLICT"


def test_publish_failure_keeps_run_failed_and_retry_converges(
    seeded_factory: sessionmaker[Session],
) -> None:
    clickhouse = MemoryClickHouse()
    service = _build_service(seeded_factory, clickhouse=clickhouse)
    clickhouse.fail_insert = True
    with pytest.raises(RuntimeError):
        service.sync(_scheduled())
    runs = service.list_runs(status="FAILED")
    assert runs and runs[0].status == "FAILED"
    clickhouse.fail_insert = False
    # 同一 scheduled_at 重试：run_key 相同，租约未过期时被拒——用新 flow_run_id 但
    # 已 FAILED 的 run 通过既有认领逻辑重开尝试（claim 返回 RUNNING 并新建 attempt）
    result = service.sync(_scheduled())
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.added_count == 4


def test_internal_query_and_diagnostics_apis(
    seeded_factory: sessionmaker[Session],
) -> None:
    service = _build_service(seeded_factory)
    service.sync(_scheduled())
    runs = service.list_runs(target_trade_date=_TARGET)
    assert runs and runs[0].status == "SUCCEEDED"
    attempts = service.list_attempts(run_id=runs[0].run_id)
    assert attempts and attempts[0].received_count == 4
    issues = service.list_issues(attempt_id=attempts[0].attempt_id)
    assert issues == ()
    with seeded_factory() as session:
        registered = session.scalar(select(IndexCurrent.index_id))
        assert registered is not None
