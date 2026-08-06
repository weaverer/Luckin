"""股东数据可观测性集成测试（T023）：issue 脱敏与日志白名单。

断言：UNKNOWN_STOCK_IDENTITY 问题的脱敏摘要不含敏感内容、哈希定位字段
存在；错误消息中的秘密在信封→Adapter 映射层即被剥离（NFR-005/006）；
按 quickstart §7 五步排障链路（run → attempt → issue → 日志 →
ClickHouse）可执行。
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.market_data import MarketDataSyncIssue, MarketDataSyncRun
from tests.contract.shareholder_data_memory import MemoryShareholderDataProvider
from tests.integration.test_shareholder_data_sync import _build_service, _cleanup, _seed_watermark
from tests.unit.test_shareholder_data_service import scheduled


def test_issue_redaction_and_traceability(
    sqlite_session_factory: sessionmaker[Session],
) -> None:
    # 未注册 ts_code → UNKNOWN_STOCK_IDENTITY 隔离 + issue 脱敏
    provider = MemoryShareholderDataProvider(
        codes=("999999.SH", "000001.SZ", "300750.SZ", "830799.BJ")
    )
    service, _, clickhouse = _build_service(sqlite_session_factory, provider=provider)
    try:
        _seed_watermark(clickhouse, date(2026, 7, 30), date(2026, 7, 29))
        result = service.sync_top10_holders(scheduled())
        assert result.invalid_count == 1
        with sqlite_session_factory.begin() as session:
            issue = (
                session.query(MarketDataSyncIssue)
                .filter_by(category="UNKNOWN_STOCK_IDENTITY")
                .one()
            )
            run = session.query(MarketDataSyncRun).filter_by(
                run_id=result.run_id
            ).one()
        assert issue.provider_security_id_hash  # 哈希定位，不存原文
        assert issue.security_code == "999999.SH"  # 白名单定位字段
        assert "token" not in issue.safe_summary.lower()
        assert "999999.SH" not in issue.safe_summary  # 摘要脱敏
        assert run.status == "SUCCEEDED"
        # 五步排障链路：run → attempt → issue 可关联（quickstart §7）
        assert issue.attempt_id == result.attempt_id
        assert run.run_id == result.run_id
    finally:
        _cleanup(clickhouse)


def test_error_summary_sanitized_at_source() -> None:
    """业务错误消息中的秘密不得进入映射后的错误摘要（NFR-005/006）。"""

    import httpx

    from lucking.integrations.tushare.client import TushareClient
    from lucking.integrations.tushare.shareholder_data_provider import (
        TushareShareholderDataProvider,
    )
    from lucking.models.shareholder_data import ShareholderDataRequest
    from lucking.ports.market_data_common import ProviderError

    secret = "SECRET-TOKEN-abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        # 上游业务错误消息含秘密（真实场景：token 拼接进错误文本）
        return httpx.Response(
            200,
            json={
                "code": 20002,
                "msg": f"token 无效：{secret}",
                "data": {"fields": [], "items": []},
            },
        )

    client = TushareClient(token="test-token", transport=httpx.MockTransport(handler))
    provider = TushareShareholderDataProvider(client)
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch_top10_holders(
            ShareholderDataRequest(date(2026, 4, 30), "TOP10"),
            deadline=1_000_000_000_000.0,
        )
    assert secret not in excinfo.value.summary  # 信封已按类别生成脱敏摘要
    assert secret not in str(excinfo.value)
    assert excinfo.value.category == "AUTHENTICATION"
