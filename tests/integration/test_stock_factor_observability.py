"""股票技术面因子可观测性验证：审计三表关联、问题脱敏与日志白名单。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.db import Base
from lucking.logging import JsonlLogStore
from lucking.models.market_data import MarketDataSyncIssue
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.models.trading_calendar import TradingCalendar
from lucking.repositories.market_data import SqlAlchemyMarketDataRepository
from lucking.services.stock_factor import (
    BackfillStockFactorCommand,
    StockFactorService,
    StockFactorSyncStatus,
)
from tests.contract.stock_factor_memory import MemoryClickHouse, MemoryStockFactorProvider

_TARGET = date(2024, 1, 2)


def _factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory.begin() as session:
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
        session.add(
            StockCurrent(
                stock_id="stock-600000",
                market_code="CN-S",
                venue_code="XSHG",
                security_code="600000",
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
                provider_security_id="600000.SH",
                stock_id="stock-600000",
                last_seen_run_id="seed",
                last_seen_at=now,
                created_at=now,
            )
        )
    return factory


def test_audit_linkage_and_issue_sanitization() -> None:
    factory = _factory()
    # 999999.SH 后缀合法但未在 003 主数据注册 → UNKNOWN_STOCK_IDENTITY
    service = StockFactorService(
        MemoryStockFactorProvider(codes=("600000.SH", "999999.SH")),
        SqlAlchemyMarketDataRepository(factory),
        MemoryClickHouse(),
        factory,
    )
    result = service.sync(
        BackfillStockFactorCommand(_TARGET, f"obs-{uuid4().hex[:6]}", str(uuid4()))
    )
    assert result.status is StockFactorSyncStatus.SUCCEEDED
    assert result.invalid_count == 1
    # 五分钟排障链路：run → attempt → issue 全关联
    runs = service.list_runs(target_trade_date=_TARGET)
    assert runs and runs[0].status == "SUCCEEDED"
    attempts = service.list_attempts(run_id=runs[0].run_id)
    assert attempts and attempts[0].received_count == 2
    issues = service.list_issues(attempt_id=attempts[0].attempt_id)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.category == "UNKNOWN_STOCK_IDENTITY"
    assert "token" not in issue.safe_summary.lower()
    # 脱敏：库中只保存标识哈希与安全定位代码，不保存完整请求行
    with factory() as session:
        row = session.scalar(
            select(MarketDataSyncIssue).where(
                MarketDataSyncIssue.issue_id == issue.issue_id
            )
        )
        assert row is not None
        assert row.provider_security_id_hash == hashlib.sha256(
            b"999999.SH"
        ).hexdigest()
        assert row.safe_summary == issue.safe_summary


def test_log_store_whitelist_drops_secrets(tmp_path: Path) -> None:
    store = JsonlLogStore(tmp_path, filename="stock-factor-sync.jsonl")
    store.write(
        "stock_factor_sync_failed",
        level="ERROR",
        flow_run_id=str(uuid4()),
        error_category="AUTHENTICATION",
        error_summary="凭据无效",
        token="super-secret-token",
        api_key="another-secret",
        raw_request="full-body",
    )
    lines = list((tmp_path / "stock-factor-sync.jsonl").read_text().splitlines())
    assert lines
    payload = json.loads(lines[0])
    assert payload["error_category"] == "AUTHENTICATION"
    assert "token" not in payload
    assert "api_key" not in payload
    assert "raw_request" not in payload
    assert "super-secret-token" not in json.dumps(payload, ensure_ascii=False)
