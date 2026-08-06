"""TushareStockFactorProvider 供应商契约测试（MockTransport 信封替身）。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from lucking.integrations.tushare.client import TushareClient
from lucking.integrations.tushare.stock_factor_provider import (
    PROVIDER_STOCK_FACTOR_FIELDS,
    TushareStockFactorProvider,
)
from lucking.models.stock_factor import STOCK_FACTOR_FIELDS, StockFactorRequest
from lucking.ports.market_data_common import (
    ProviderDeadlineExceededError,
    ProviderEmptyAggregateError,
    ProviderPayloadError,
    ProviderRateLimitedError,
    ProviderResponseCappedError,
)

_TARGET = date(2026, 8, 3)
_REQUEST = StockFactorRequest(_TARGET)


def _envelope(
    rows: list[list[object]],
    *,
    fields: list[str] | None = None,
    code: int = 0,
    msg: str = "",
) -> dict[str, object]:
    return {
        "code": code,
        "msg": msg,
        "data": {
            "fields": fields or list(PROVIDER_STOCK_FACTOR_FIELDS),
            "items": rows,
        },
    }


def _row(
    *,
    ts_code: str = "600000.SH",
    trade_date: str = "20260803",
    extra: dict[str, object] | None = None,
) -> list[object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "close": 10.5,
    }
    for field in STOCK_FACTOR_FIELDS:
        row[field] = None
    row.update(
        {
            "open": 10.2,
            "open_qfq": 10.2,
            "open_hfq": 22.8,
            "close_qfq": 10.5,
            "close_hfq": 23.4,
            "pre_close": 10.4,
            "change": 0.1,
            "pct_chg": 0.96,
            "vol": 123456.0,
            "amount": 129612.0,
            "turnover_rate": 1.23,
            "volume_ratio": 0.9,
            "adj_factor": 2.23,
            "pe_ttm": 8.5,
            "ma_bfq_5": 10.4,
            "macd_bfq": 0.12,
            "kdj_bfq": 55.0,
            "kdj_qfq": 55.0,
            "updays": 3,
        }
    )
    if extra:
        row.update(extra)
    return [row.get(field) for field in PROVIDER_STOCK_FACTOR_FIELDS]


def _provider(
    handler: object,
    *,
    monotonic: object | None = None,
    sleep: object | None = None,
) -> TushareStockFactorProvider:
    kwargs: dict[str, object] = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if sleep is not None:
        kwargs["sleep"] = sleep
    client = TushareClient(token="test-token", transport=httpx.MockTransport(handler))
    return TushareStockFactorProvider(client, **kwargs)  # type: ignore[arg-type]


def test_request_params_only_trade_date_and_identity_split() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["params"] = payload["params"]
        captured["fields"] = payload["fields"]
        return httpx.Response(200, json=_envelope([_row()]))

    provider = _provider(handler)
    batch = provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)
    assert captured["params"] == {"trade_date": "20260803"}
    assert captured["fields"] == ",".join(PROVIDER_STOCK_FACTOR_FIELDS)
    record = batch.records[0]
    assert record.provider_security_id == "600000.SH"
    assert record.venue_code == "XSHG"
    assert record.security_code == "600000"
    assert record.trade_date == _TARGET
    assert record.close == Decimal("10.5")
    assert record.values["close_qfq"] == Decimal("10.5")  # 复权变体保留后缀
    assert record.values["close_hfq"] == Decimal("23.4")
    assert record.values["adj_factor"] == Decimal("2.23")
    assert record.values["ma_bfq_5"] == Decimal("10.4")  # 前缀式周期命名
    assert record.values["kdj_bfq"] == Decimal("55.0")  # 后缀式变体命名
    assert record.values["updays"] == 3
    assert isinstance(record.values["updays"], int)  # 天数因子必须为整数
    assert record.values["pe"] is None  # 来源未返回的因子以 None 保存


def test_venue_split_for_all_three_exchanges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            _row(ts_code="600000.SH"),
            _row(ts_code="000001.SZ"),
            _row(ts_code="830799.BJ"),
        ]
        return httpx.Response(200, json=_envelope(rows))

    provider = _provider(handler)
    batch = provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)
    assert [r.venue_code for r in batch.records] == ["XSHG", "XSHE", "XBSE"]


def test_missing_close_is_isolated_not_fatal() -> None:
    """收盘价缺失 = 当日无行情（停牌/新股缺因子）：单条隔离，不阻断整批。"""
    def handler(request: httpx.Request) -> httpx.Response:
        valid = _row(ts_code="600000.SH")
        no_quote = _row(ts_code="000001.SZ", extra={"close": None})
        return httpx.Response(200, json=_envelope([valid, no_quote]))

    provider = _provider(handler)
    batch = provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)
    assert len(batch.records) == 1
    assert batch.records[0].provider_security_id == "600000.SH"
    assert len(batch.isolated) == 1
    assert batch.isolated[0].category == "INVALID_FIELD"
    assert batch.isolated[0].provider_security_id == "000001.SZ"
    assert batch.evidence.received_count == 2


def test_field_whitelist_strict_mismatch_fails_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        row = _row(extra={"unexpected_field": 1})
        fields = [*PROVIDER_STOCK_FACTOR_FIELDS, "unexpected_field"]
        return httpx.Response(200, json=_envelope([row], fields=fields))

    provider = _provider(handler)
    with pytest.raises(ProviderPayloadError):
        provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)


def test_empty_response_is_empty_aggregate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([]))

    provider = _provider(handler)
    with pytest.raises(ProviderEmptyAggregateError):
        provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)


def test_capped_at_10000_rows_fails() -> None:
    rows = [_row(ts_code=f"6000{i:02d}.SH") for i in range(1, 10001)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(rows))

    provider = _provider(handler)
    with pytest.raises(ProviderResponseCappedError):
        provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)


def test_rate_limited_maps_and_retries_with_backoff() -> None:
    attempts: list[int] = []
    delays: list[float] = []
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        delays.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        if len(attempts) <= 1:
            return httpx.Response(200, json=_envelope([], code=20001, msg="访问频率超限"))
        return httpx.Response(200, json=_envelope([_row()]))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    batch = provider.fetch_stock_factors(_REQUEST, deadline=100000.0)
    assert batch.evidence.retry_count == 1
    assert delays == [30.0]  # 首次限流退避 30 秒
    assert batch.records[0].provider_security_id == "600000.SH"


def test_retry_exhausted_raises_rate_limited() -> None:
    attempts: list[int] = []
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        return httpx.Response(200, json=_envelope([], code=20001, msg="访问频率超限"))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    with pytest.raises(ProviderRateLimitedError):
        provider.fetch_stock_factors(_REQUEST, deadline=100000.0)
    assert len(attempts) == 4  # 初次 + 3 次重试


def test_deadline_enforced_before_and_after_throttle() -> None:
    clock = {"now": 5000.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("deadline 内不应真正请求")

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    with pytest.raises(ProviderDeadlineExceededError):
        provider.fetch_stock_factors(_REQUEST, deadline=5000.0)


def test_throttle_enforces_min_interval_between_calls() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([_row()]))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    provider.fetch_stock_factors(_REQUEST, deadline=100000.0)
    provider.fetch_stock_factors(_REQUEST, deadline=100000.0)
    assert sleeps and sleeps[-1] >= 2.0 - 1e-9  # 默认 30 次/分钟 → 最小间隔 2 秒


@pytest.mark.parametrize(
    ("message", "category"),
    (
        ("积分不足，请充值", "QUOTA_EXCEEDED"),
        ("token 无效", "AUTHENTICATION"),
        ("参数错误", "PROVIDER_REQUEST"),
    ),
)
def test_quota_authentication_and_request_errors_are_non_retryable(
    message: str, category: str
) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(len(attempts) + 1)
        return httpx.Response(200, json=_envelope([], code=20002, msg=message))

    provider = _provider(handler)
    with pytest.raises(Exception) as excinfo:
        provider.fetch_stock_factors(_REQUEST, deadline=1_000_000_000_000.0)
    assert getattr(excinfo.value, "category", "") == category
    assert len(attempts) == 1  # 确定性错误零重试
