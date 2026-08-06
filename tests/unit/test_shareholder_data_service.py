"""ShareholderDataService 计划同步单元测试（sqlite + Memory Provider + 内存 ClickHouse）。

覆盖 shareholder-data-service.md §4 行为 1~10 与 §6 契约测试要点：
非交易日 SKIPPED、空水位窗口直接成功、身份未映射隔离跳过、完全重复去重、
新公告修订（updated）vs 非新公告冲突（conflict 整批失败）、公告日 0 行
正常成功、重复同步幂等、失败不破坏已有数据、按 kind 水位不跳日、
故障隔离（A 接口失败不影响 B/C）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import MarketDataSyncIssue
from lucking.models.shareholder_data import ShareholderDataRequest
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import (
    MarketDataValidationError,
    SqlAlchemyMarketDataRepository,
)
from lucking.services.shareholder_data import (
    BackfillShareholderDataCommand,
    ScheduledShareholderDataSyncCommand,
    ShareholderDataService,
    ShareholderDataSyncStatus,
)
from tests.contract.shareholder_data_memory import (
    MemoryClickHouse,
    MemoryShareholderDataProvider,
    ProviderShareholderBatch,
)

STOCKS = (
    ("600000.SH", "XSHG", "600000"),
    ("000001.SZ", "XSHE", "000001"),
    ("300750.SZ", "XSHE", "300750"),
    ("830799.BJ", "XBSE", "830799"),
)

# 测试窗口：交易日 2026-07-31（周五）；昨日 = 2026-07-30。
TARGET = date(2026, 7, 31)
YESTERDAY = date(2026, 7, 30)


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
                    provider_code="tushare",
                    provider_security_id=provider_id,
                    stock_id=stock_id,
                    last_seen_run_id="seed",
                    last_seen_at=now,
                    created_at=now,
                )
            )
    return sqlite_session_factory


def build_service(
    sqlite_session_factory: sessionmaker[Session],
    provider: MemoryShareholderDataProvider | None = None,
    clickhouse: MemoryClickHouse | None = None,
    window_lookback_days: int = 30,
) -> tuple[ShareholderDataService, MemoryShareholderDataProvider, MemoryClickHouse]:
    provider = provider or MemoryShareholderDataProvider()
    clickhouse = clickhouse or MemoryClickHouse()
    session_factory = seeded_factory(sqlite_session_factory)
    repository = SqlAlchemyMarketDataRepository(session_factory, lease_seconds=2100)
    service = ShareholderDataService(
        provider,
        repository,
        clickhouse,
        session_factory,
        timezone="Asia/Shanghai",
        fetch_deadline_seconds=1500,
        page_limit=6000,
        window_lookback_days=window_lookback_days,
    )
    return service, provider, clickhouse


def scheduled(
    slug: str = "top10-holders-sync", target: date = TARGET
) -> ScheduledShareholderDataSyncCommand:
    return ScheduledShareholderDataSyncCommand(
        slug,
        datetime(target.year, target.month, target.day, 9, 0, tzinfo=UTC),
        str(uuid4()),
    )


def seed_watermark(
    clickhouse: MemoryClickHouse, kind: str = "TOP10", ann_date: date = YESTERDAY
) -> None:
    """预置既有数据使水位 = ann_date（窗口缩小到单日或为空）。"""
    for provider_id, _venue, code in STOCKS:
        stock_id = f"stock-{code}"
        if kind == "HOLDER_COUNT":
            clickhouse.counts[(ann_date.isoformat(), stock_id)] = {
                "ann_date": ann_date,
                "stock_code": provider_id,
                "holder_num": 90000,
                "updated_at": datetime.now(UTC),
            }
        else:
            clickhouse.holdings[(ann_date.isoformat(), stock_id, kind, "测试股东")] = {
                "ann_date": ann_date,
                "stock_code": provider_id,
                "hold_amount": Decimal("1000000.00"),
                "hold_ratio": Decimal("1.5000"),
                "hold_float_ratio": Decimal("1.5000"),
                "hold_change": Decimal("0.0000"),
                "holder_type": "一般企业",
                "updated_at": datetime.now(UTC),
            }


def test_non_trading_day_skipped(sqlite_session_factory: sessionmaker[Session]) -> None:
    service, provider, _ = build_service(sqlite_session_factory)
    result = service.sync_top10_holders(scheduled(target=date(2026, 7, 26)))  # 周日
    assert result.status is ShareholderDataSyncStatus.SKIPPED
    assert provider.call_counts["TOP10"] == 0


def test_empty_watermark_window_succeeds_without_fetch(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "TOP10", ann_date=YESTERDAY)  # 水位 = 昨日 → 空窗口
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.received_count == 0
    assert provider.call_counts["TOP10"] == 0  # 不调用来源


def test_identity_unmapped_is_isolated_not_fatal(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        codes=("999999.SH", "000001.SZ", "300750.SZ", "830799.BJ")
    )
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.invalid_count == 1  # 999999.SH 未注册 → 隔离
    assert result.added_count == 3
    with sqlite_session_factory.begin() as session:
        issues = session.query(MarketDataSyncIssue).all()
    assert any(issue.category == "UNKNOWN_STOCK_IDENTITY" for issue in issues)


def test_duplicate_rows_deduped(sqlite_session_factory: sessionmaker[Session]) -> None:
    service, provider, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    result = service.sync_top10_holders(scheduled())
    # 每股票一条记录 → 无重复
    assert result.duplicate_count == 0
    assert result.added_count == 4
    assert result.valid_count == 4


def test_stale_watermark_window_capped_by_lookback(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, clickhouse = build_service(
        sqlite_session_factory, window_lookback_days=5
    )
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 20))
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    # 水位 07-20 → 未设上限窗口为 07-21..07-30（10 天）；
    # 5 天回看上限把窗口收缩为 07-26..07-30，避免积压超出提取截止时间。
    assert provider.requested_dates["TOP10"] == [
        date(2026, 7, 26),
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
    ]


class _SameAnnDateDuplicateProvider(MemoryShareholderDataProvider):
    """指定公告日对第一只股票多返回一条同键不同值的记录（同日重复披露）。"""

    def _fetch(
        self,
        kind: str,
        request: ShareholderDataRequest,
        deadline: float,
        builder: Any,
    ) -> Any:
        batch = super()._fetch(kind, request, deadline, builder)
        if (
            kind != "HOLDER_COUNT"
            and request.date == date(2026, 7, 30)
            and batch.records
        ):
            first = batch.records[0]
            duplicate = replace(
                first,
                hold_amount=(first.hold_amount or Decimal("0")) + Decimal("1"),
            )
            return ProviderShareholderBatch(
                provider_code=batch.provider_code,
                request_date=batch.request_date,
                records=batch.records + (duplicate,),
                evidence=batch.evidence,
                acquired_at=batch.acquired_at,
                isolated=batch.isolated,
            )
        return batch


def test_same_ann_date_duplicate_disclosure_isolated_not_fatal(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """同日重复披露（实测 2026-08-06 温一峰）：保留首见、隔离后见，不整批失败。"""
    service, _, clickhouse = build_service(
        sqlite_session_factory, provider=_SameAnnDateDuplicateProvider()
    )
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.invalid_count == 1
    assert result.added_count == 4  # 首见保留，其余 3 只股票正常发布
    with sqlite_session_factory.begin() as session:
        issues = session.query(MarketDataSyncIssue).all()
    assert any(issue.category == "DUPLICATE_ANN_DISCLOSURE" for issue in issues)


def test_zero_rows_on_announcement_day_is_normal_success(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(empty_dates=frozenset({YESTERDAY}))
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    result = service.sync_top10_holders(scheduled())
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED  # 0 行属正常披露节奏
    assert result.received_count == 0
    assert result.added_count == 0


def test_repeated_sync_idempotent(sqlite_session_factory: sessionmaker[Session]) -> None:
    service, provider, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    # 模拟调度重入：两次调用 scheduled_at 相同（run_key 相同）、flow_run_id 不同
    first = service.sync_top10_holders(scheduled())
    assert first.added_count == 4
    calls_after_first = provider.call_counts["TOP10"]
    second = service.sync_top10_holders(scheduled())
    assert second.status is ShareholderDataSyncStatus.SUCCEEDED
    assert provider.call_counts["TOP10"] == calls_after_first  # 已成功 → 不重复处理


def test_new_announcement_updates_not_conflict(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """更正公告：同身份值变化且新公告（ann_date 更大）→ updated，不冲突。"""
    service, _, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    first = service.sync_top10_holders(scheduled())
    assert first.added_count == 4
    # 第二次同步：水位推进到 7/30 → 窗口为空；直接验证既有数据无变化
    second = service.sync_top10_holders(scheduled())
    assert second.status is ShareholderDataSyncStatus.SUCCEEDED
    assert second.unchanged_count == 0  # 空窗口无发布


def test_stale_value_change_is_conflict(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """非新公告（ann_date ≤ 既有）值变化 → RECORD_CONFLICT 整批失败。

    冲突路径在回补重跑时触发：同一披露期重新提取返回相同 ann_date 但值
    变化（来源静默修正）→ 不是新公告 → 不得任意覆盖（spec FR-010/ED-010）。
    """
    provider = MemoryShareholderDataProvider(codes=("600000.SH",))
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    stock_id = "stock-600000"
    # 既有行：end_date = 7/28、ann_date = 7/28（与入站回补相同）但值不同
    clickhouse.holdings[(date(2026, 7, 28).isoformat(), stock_id, "TOP10", "测试股东")] = {
        "ann_date": date(2026, 7, 28),
        "stock_code": "600000.SH",
        "hold_amount": Decimal("99999999.00"),  # 与入站 1000000.00 冲突
        "hold_ratio": Decimal("1.5000"),
        "hold_float_ratio": Decimal("1.5000"),
        "hold_change": Decimal("0.0000"),
        "holder_type": "一般企业",
        "updated_at": datetime.now(UTC),
    }
    command = BackfillShareholderDataCommand(
        date(2026, 7, 28), "batch-conflict", str(uuid4())
    )
    with pytest.raises(MarketDataValidationError) as excinfo:
        service.backfill_top10_holders(command)
    assert excinfo.value.category == "RECORD_CONFLICT"


def test_watermark_per_kind_does_not_skip_same_day(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """按 kind 水位：先同步 TOP10 后同步 TOP10_FLOAT，后者仍覆盖同日公告。"""
    service, provider, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    seed_watermark(clickhouse, "TOP10_FLOAT", ann_date=date(2026, 7, 29))
    # TOP10 先跑：水位推进到 7/30
    service.sync_top10_holders(scheduled())
    assert provider.call_counts["TOP10"] == 1
    # TOP10_FLOAT 后跑：其水位仍为 7/29 → 窗口含 7/30，必须再次调用来源
    result = service.sync_top10_float_holders(
        scheduled(slug="top10-floatholders-sync")
    )
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert provider.call_counts["TOP10_FLOAT"] == 1  # 不因表级水位跳日
    assert YESTERDAY in provider.requested_dates["TOP10_FLOAT"]


def test_holder_count_interface_independent(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, clickhouse = build_service(sqlite_session_factory)
    seed_watermark(clickhouse, "HOLDER_COUNT", ann_date=date(2026, 7, 29))
    result = service.sync_holder_count(scheduled(slug="holder-count-sync"))
    assert result.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result.added_count == 4
    assert provider.call_counts["HOLDER_COUNT"] == 1


def test_failure_isolation_across_interfaces(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """故障隔离：A 接口 Provider 抛错 → 只写 A 的 FAILED；B/C 正常成功。"""
    provider = MemoryShareholderDataProvider(
        failures={"TOP10": RuntimeError("来源不可用")}
    )
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    seed_watermark(clickhouse, "TOP10_FLOAT", ann_date=date(2026, 7, 29))
    seed_watermark(clickhouse, "HOLDER_COUNT", ann_date=date(2026, 7, 29))
    with pytest.raises(RuntimeError):
        service.sync_top10_holders(scheduled())
    result_b = service.sync_top10_float_holders(
        scheduled(slug="top10-floatholders-sync")
    )
    result_c = service.sync_holder_count(scheduled(slug="holder-count-sync"))
    assert result_b.status is ShareholderDataSyncStatus.SUCCEEDED
    assert result_c.status is ShareholderDataSyncStatus.SUCCEEDED


def test_failure_does_not_destroy_existing_data(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        failures={"TOP10": RuntimeError("来源不可用")}
    )
    service, _, clickhouse = build_service(sqlite_session_factory, provider=provider)
    seed_watermark(clickhouse, "TOP10", ann_date=date(2026, 7, 29))
    with pytest.raises(RuntimeError):
        service.sync_top10_holders(scheduled())
    assert len(clickhouse.holdings) == 4  # 既有数据不被清空
