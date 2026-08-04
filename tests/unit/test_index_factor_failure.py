"""指数因子失败路径契约测试：限流/空响应/缺失/冲突/触顶不破坏已有数据。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.market_data_common import ProviderRateLimitedError
from lucking.repositories.index_factor_identity import IndexFactorIdentityRepository
from lucking.repositories.market_data import (
    MarketDataValidationError,
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

_TARGET = date(2024, 1, 2)


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
    return sqlite_session_factory


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    *,
    provider: MemoryIndexFactorProvider | None = None,
    clickhouse: MemoryClickHouse | None = None,
) -> IndexFactorService:
    return IndexFactorService(
        provider or MemoryIndexFactorProvider(),
        SqlAlchemyMarketDataRepository(sqlite_session_factory),
        IndexFactorIdentityRepository(sqlite_session_factory),
        clickhouse or MemoryClickHouse(),
        sqlite_session_factory,
    )


def _backfill(service: IndexFactorService, target: date = _TARGET) -> object:
    return service.sync(
        BackfillIndexFactorCommand(target, f"fail-{uuid4().hex[:8]}", str(uuid4()))
    )


def test_rate_limited_failure_records_failed_run_and_no_publish(
    failure_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    clickhouse = MemoryClickHouse()
    service = _build_service(failure_factory, provider=provider, clickhouse=clickhouse)
    with pytest.raises(ProviderRateLimitedError):
        _backfill(service)
    runs = service.list_runs(status="FAILED")
    assert runs and runs[0].status == "FAILED"
    assert clickhouse.published == []  # 失败不发布任何数据


def test_empty_response_fails_as_empty_aggregate(
    failure_factory: sessionmaker[Session],
) -> None:
    class EmptyProvider(MemoryIndexFactorProvider):
        def __init__(self) -> None:
            super().__init__(codes=())

    service = _build_service(failure_factory, provider=EmptyProvider())
    with pytest.raises(MarketDataValidationError) as excinfo:
        _backfill(service)
    assert excinfo.value.category == "EMPTY_AGGREGATE"


def test_individual_index_without_data_is_normal_success(
    failure_factory: sessionmaker[Session],
) -> None:
    """个别指数当日无数据（挂起）属正常业务结果；全市场空响应才失败。"""
    provider = MemoryIndexFactorProvider(suspended=frozenset({"399001.SZ"}))
    service = _build_service(failure_factory, provider=provider)
    result = _backfill(service)
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.received_count == 3
    assert result.valid_count == 3
    assert result.invalid_count == 0


def test_no_quote_rows_are_isolated_with_issue(
    failure_factory: sessionmaker[Session],
) -> None:
    """来源返回行但基础行情为空（当日无行情）→ 单条隔离计数，不阻断整批。"""
    provider = MemoryIndexFactorProvider(no_quote_codes=frozenset({"399001.SZ"}))
    service = _build_service(failure_factory, provider=provider)
    result = _backfill(service)
    assert result.status is IndexFactorSyncStatus.SUCCEEDED
    assert result.received_count == 4
    assert result.valid_count == 3
    assert result.invalid_count == 1
    issues = service.list_issues(attempt_id=result.attempt_id)
    assert len(issues) == 1
    assert issues[0].category == "INVALID_FIELD"


def test_failure_after_success_keeps_existing_data(
    failure_factory: sessionmaker[Session],
) -> None:
    provider = MemoryIndexFactorProvider()
    clickhouse = MemoryClickHouse()
    service = _build_service(failure_factory, provider=provider, clickhouse=clickhouse)
    first = _backfill(service, date(2024, 1, 3))
    assert first.status is IndexFactorSyncStatus.SUCCEEDED
    published_before = len(clickhouse.published)
    # 另一日期失败：已有成功数据不受影响
    provider.fail_with = ProviderRateLimitedError("memory", "演练注入限流")
    with pytest.raises(ProviderRateLimitedError):
        _backfill(service, date(2024, 1, 4))
    assert len(clickhouse.published) == published_before
    runs = service.list_runs(target_trade_date=date(2024, 1, 3))
    assert runs and runs[0].status == "SUCCEEDED"


def test_capped_evidence_fails_as_continuation_incomplete(
    failure_factory: sessionmaker[Session],
) -> None:
    class CappedProvider(MemoryIndexFactorProvider):
        def fetch_index_factors(self, request: object, *, deadline: float) -> object:
            from lucking.models.index_factor import ProviderIndexFactorBatch
            from lucking.models.market_data import RetrievalEvidence

            batch = super().fetch_index_factors(request, deadline=deadline)  # type: ignore[arg-type]
            capped = RetrievalEvidence(
                request_count=1,
                completed_request_count=1,
                retry_count=0,
                page_count=1,
                page_limit=8000,
                last_page_count=8000,
                received_count=8000,
                pagination_enabled=False,
                continuation_exhausted=True,
                repeated_page_detected=False,
            )
            return ProviderIndexFactorBatch(
                provider_code=self.provider_code,
                target_trade_date=batch.target_trade_date,
                records=batch.records,
                evidence=capped,
                acquired_at=batch.acquired_at,
            )

    service = _build_service(failure_factory, provider=CappedProvider())
    with pytest.raises(MarketDataValidationError) as excinfo:
        _backfill(service)
    assert excinfo.value.category == "CONTINUATION_INCOMPLETE"
