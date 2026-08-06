"""股东数据增量同步端到端集成测试（sqlite 审计/身份 + 真实 ClickHouse 发布）。

沿用 007 的集成模式（tests/integration/test_stock_factor_sync.py）：审计与
身份在 sqlite 内存库，ClickHouse 为真实实例。每测试使用唯一标识与按标识
清理，避免共享 ClickHouse 表跨测试污染。
验证：认领幂等（重复 scheduled_at 不重复处理）、发布计数正确、更正公告
修订（新公告 updated）生效、失败终态不破坏已有数据。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.repositories.shareholder_data_clickhouse import (
    ShareholderDataClickHouseRepository,
)
from lucking.services.shareholder_data import (
    ScheduledShareholderDataSyncCommand,
    ShareholderDataService,
    ShareholderDataSyncStatus,
)
from tests.contract.shareholder_data_memory import MemoryShareholderDataProvider
from tests.unit.test_shareholder_data_service import seeded_factory

_STOCKS = (
    ("600000.SH", "XSHG", "600000"),
    ("000001.SZ", "XSHE", "000001"),
    ("300750.SZ", "XSHE", "300750"),
    ("830799.BJ", "XBSE", "830799"),
)
# 测试窗口：交易日 2026-07-31（周五）；昨日 = 2026-07-30
_YESTERDAY = date(2026, 7, 30)
_UNIQUE = uuid4().hex[:8]


def _client(settings: Settings) -> ClickHouseClient:
    return ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=(
            settings.clickhouse_password.get_secret_value()
            if settings.clickhouse_password is not None
            else None
        ),
    )


_TEST_STOCK_IDS = "('stock-600000', 'stock-000001', 'stock-300750', 'stock-830799')"


def _cleanup(clickhouse: ClickHouseClient) -> None:
    # 入站记录的 stock_code 为纯 ts_code（无 _UNIQUE 后缀），必须同时按
    # 测试专属 stock_id 命名空间清理，防止残留数据推高水位污染后续运行。
    for table in ("shareholder_holding", "shareholder_count"):
        clickhouse.execute(
            f"ALTER TABLE lucking.{table} DELETE WHERE stock_code LIKE '%{_UNIQUE}%' "
            f"OR stock_id IN {_TEST_STOCK_IDS}"
        )


def _seed_watermark(clickhouse: ClickHouseClient, end_date: date, ann_date: date) -> None:
    rows = []
    for provider_id, _venue, code in _STOCKS:
        rows.append(
            {
                "end_date": end_date,
                "stock_id": f"stock-{code}",
                "holder_kind": "TOP10",
                "holder_name": "测试股东",
                "ann_date": ann_date,
                "stock_code": f"{provider_id}-{_UNIQUE}",
                "hold_amount": Decimal("1000000.00"),
                "hold_ratio": Decimal("1.5000"),
                "hold_float_ratio": Decimal("1.5000"),
                "hold_change": Decimal("0.0000"),
                "holder_type": "一般企业",
                "updated_at": datetime.now(UTC).replace(tzinfo=None),
            }
        )
    clickhouse.insert_rows(
        "shareholder_holding",
        (
            "end_date",
            "stock_id",
            "holder_kind",
            "holder_name",
            "ann_date",
            "stock_code",
            "hold_amount",
            "hold_ratio",
            "hold_float_ratio",
            "hold_change",
            "holder_type",
            "updated_at",
        ),
        rows,
    )


def _build_service(
    sqlite_session_factory: sessionmaker[Session],
    provider: MemoryShareholderDataProvider | None = None,
) -> tuple[ShareholderDataService, MemoryShareholderDataProvider, ClickHouseClient]:
    provider = provider or MemoryShareholderDataProvider(
        codes=tuple(p for p, _v, _c in _STOCKS)
    )
    settings = Settings()
    migrate(settings)
    clickhouse = _client(settings)
    _cleanup(clickhouse)
    session_factory = seeded_factory(sqlite_session_factory)
    # 扩展交易日历：8/3（周一）开放，供跨月修订验证使用
    from lucking.models.trading_calendar import TradingCalendar

    now = datetime.now(UTC).replace(tzinfo=None)
    with session_factory.begin() as session:
        session.add(
            TradingCalendar(
                market_code="CN-S",
                calendar_date=date(2026, 8, 3),
                is_open=True,
                previous_open_date=None,
                source="tushare",
                source_market="CN-S",
                sync_mode="monthly",
                created_at=now,
                updated_at=now,
            )
        )
    repository = SqlAlchemyMarketDataRepository(session_factory, lease_seconds=2100)
    service = ShareholderDataService(
        provider,
        repository,
        ShareholderDataClickHouseRepository(clickhouse),
        session_factory,
        timezone="Asia/Shanghai",
        fetch_deadline_seconds=1500,
        page_limit=6000,
    )
    return service, provider, clickhouse


def _scheduled(
    slug: str = "top10-holders-sync", target: date = date(2026, 7, 31)
) -> ScheduledShareholderDataSyncCommand:
    return ScheduledShareholderDataSyncCommand(
        slug,
        datetime(target.year, target.month, target.day, 9, 0, tzinfo=UTC),
        str(uuid4()),
    )


def test_incremental_sync_end_to_end_and_idempotent(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    service, provider, clickhouse = _build_service(sqlite_session_factory)
    try:
        _seed_watermark(clickhouse, _YESTERDAY, date(2026, 7, 29))
        first = service.sync_top10_holders(_scheduled())
        assert first.status is ShareholderDataSyncStatus.SUCCEEDED
        assert first.added_count == 4
        rows = clickhouse.execute(
            "SELECT count() AS count FROM lucking.shareholder_holding FINAL "
            f"WHERE stock_id IN {_TEST_STOCK_IDS} AND holder_kind = 'TOP10'"
        )
        assert rows[0]["count"] == 8  # 4 条水位预置 + 4 条新增
        calls_after_first = provider.call_counts["TOP10"]
        # 幂等：相同 scheduled_at（不同 flow_run_id）→ 不重复处理
        second = service.sync_top10_holders(_scheduled())
        assert second.status is ShareholderDataSyncStatus.SUCCEEDED
        assert provider.call_counts["TOP10"] == calls_after_first
    finally:
        _cleanup(clickhouse)


def test_revision_update_new_announcement(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    """真实 ClickHouse 修订语义：同一身份出现新公告（ann 更大）值变化 → updated。"""
    provider = MemoryShareholderDataProvider(
        codes=tuple(p for p, _v, _c in _STOCKS),
        value_overrides={
            date(2026, 7, 31): {"hold_amount": Decimal("2000000.00")}  # 更正公告值
        },
    )
    service, _, clickhouse = _build_service(sqlite_session_factory, provider=provider)
    try:
        _seed_watermark(clickhouse, _YESTERDAY, date(2026, 7, 29))
        first = service.sync_top10_holders(_scheduled())  # 目标 7/31，窗口 (7/29, 7/30]
        assert first.added_count == 4
        # 第二次同步目标 8/3：窗口 (7/30, 8/2] → 7/31 更正公告（值不同、ann 更大）
        second = service.sync_top10_holders(
            ScheduledShareholderDataSyncCommand(
                "top10-holders-sync",
                datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
                str(uuid4()),
            )
        )
        assert second.status is ShareholderDataSyncStatus.SUCCEEDED
        assert second.updated_count == 4  # 新公告 → 按最新值更新，不冲突
        rows = clickhouse.execute(
            "SELECT hold_amount FROM lucking.shareholder_holding FINAL "
            f"WHERE stock_id IN {_TEST_STOCK_IDS} AND holder_kind = 'TOP10' "
            "AND holder_name = '测试股东' AND end_date = '2026-07-28' LIMIT 1"
        )
        assert float(rows[0]["hold_amount"]) == 2000000.00  # 修订值生效
    finally:
        _cleanup(clickhouse)


def test_failure_terminal_state_preserves_data(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    provider = MemoryShareholderDataProvider(
        codes=tuple(p for p, _v, _c in _STOCKS),
        failures={"TOP10": RuntimeError("来源不可用")},
    )
    service, _, clickhouse = _build_service(sqlite_session_factory, provider=provider)
    try:
        _seed_watermark(clickhouse, _YESTERDAY, date(2026, 7, 29))
        with pytest.raises(RuntimeError):
            service.sync_top10_holders(_scheduled())
        # 既有数据不受影响
        rows = clickhouse.execute(
            "SELECT count() AS count FROM lucking.shareholder_holding FINAL "
            f"WHERE stock_code LIKE '%{_UNIQUE}%'"
        )
        assert rows[0]["count"] == 4  # 只有水位预置
    finally:
        _cleanup(clickhouse)
