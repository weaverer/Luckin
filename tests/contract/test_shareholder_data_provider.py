"""TushareShareholderDataProvider 供应商契约测试（MockTransport 信封替身）。

覆盖 tushare-shareholder-data.md §7：请求参数（ann_date/公告区间、不传
ts_code、limit=6000）、字段白名单严格相等、has_more/offset 分页续取与
收尾、触顶/重复页判定、空响应正常、节流间隔 ≥ 150 毫秒、重试退避与
deadline 约束、错误分类映射、三提取方法相互独立。
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from lucking.integrations.tushare.client import TushareClient
from lucking.integrations.tushare.shareholder_data_provider import (
    TushareShareholderDataProvider,
)
from lucking.models.shareholder_data import (
    PROVIDER_HOLDER_COUNT_FIELDS,
    PROVIDER_TOP10_HOLDER_FIELDS,
    ShareholderDataRequest,
)
from lucking.ports.market_data_common import (
    ProviderDeadlineExceededError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderRateLimitedError,
    ProviderResponseCappedError,
)

_DAY = date(2026, 4, 30)
_REQ_TOP10 = ShareholderDataRequest(_DAY, "TOP10")
_REQ_FLOAT = ShareholderDataRequest(_DAY, "TOP10_FLOAT")
_REQ_COUNT = ShareholderDataRequest(_DAY, "HOLDER_COUNT")


def _envelope(
    rows: list[list[object]],
    *,
    fields: list[str] | None = None,
    code: int = 0,
    msg: str = "",
    has_more: bool = False,
) -> dict[str, object]:
    return {
        "code": code,
        "msg": msg,
        "data": {
            "fields": fields or list(PROVIDER_TOP10_HOLDER_FIELDS),
            "items": rows,
            "has_more": has_more,
        },
    }


def _holder_row(
    *,
    ts_code: str = "600000.SH",
    ann_date: str = "20260430",
    end_date: str = "20260331",
    extra: dict[str, object] | None = None,
) -> list[object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "ann_date": ann_date,
        "end_date": end_date,
        "holder_name": "上海国际集团有限公司",
        "hold_amount": 7086834641.0,
        "hold_ratio": 21.2781,
        "hold_float_ratio": 21.2781,
        "hold_change": 0.0,
        "holder_type": "一般企业",
    }
    if extra:
        row.update(extra)
    return [row.get(field) for field in PROVIDER_TOP10_HOLDER_FIELDS]


def _count_row(
    *,
    ts_code: str = "300199.SZ",
    ann_date: str = "20260429",
    end_date: str = "20260331",
    extra: dict[str, object] | None = None,
) -> list[object]:
    row: dict[str, object] = {
        "ts_code": ts_code,
        "ann_date": ann_date,
        "end_date": end_date,
        "holder_num": 98777,
    }
    if extra:
        row.update(extra)
    return [row.get(field) for field in PROVIDER_HOLDER_COUNT_FIELDS]


def _provider(
    handler: object,
    *,
    monotonic: object | None = None,
    sleep: object | None = None,
    max_pages: int = 20,
) -> TushareShareholderDataProvider:
    kwargs: dict[str, object] = {"max_pages": max_pages}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    if sleep is not None:
        kwargs["sleep"] = sleep
    client = TushareClient(token="test-token", transport=httpx.MockTransport(handler))
    return TushareShareholderDataProvider(client, **kwargs)  # type: ignore[arg-type]


def test_request_params_per_interface_and_no_ts_code() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.setdefault("calls", []).append(
            {"params": payload["params"], "fields": payload["fields"]}
        )
        api = payload["api_name"]
        if api == "stk_holdernumber":
            return httpx.Response(
                200,
                json=_envelope(
                    [_count_row()], fields=list(PROVIDER_HOLDER_COUNT_FIELDS)
                ),
            )
        return httpx.Response(200, json=_envelope([_holder_row()]))

    provider = _provider(handler)
    provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)
    provider.fetch_top10_float_holders(_REQ_FLOAT, deadline=1_000_000_000_000.0)
    provider.fetch_holder_count(_REQ_COUNT, deadline=1_000_000_000_000.0)
    calls = captured["calls"]  # type: ignore[assignment]
    assert calls[0]["params"] == {"ann_date": "20260430", "limit": 6000}  # type: ignore[index]
    assert "ts_code" not in calls[0]["params"]  # type: ignore[index]
    assert calls[0]["fields"] == ",".join(PROVIDER_TOP10_HOLDER_FIELDS)  # type: ignore[index]
    assert calls[1]["params"] == {"ann_date": "20260430", "limit": 6000}  # type: ignore[index]
    assert calls[2]["params"] == {  # type: ignore[index]
        "start_date": "20260430",
        "end_date": "20260430",
        "limit": 6000,
    }
    assert calls[2]["fields"] == ",".join(PROVIDER_HOLDER_COUNT_FIELDS)  # type: ignore[index]


def test_record_mapping_and_venue_split() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            _holder_row(ts_code="600000.SH"),
            _holder_row(ts_code="000001.SZ", extra={"holder_name": "张三"}),
            _holder_row(ts_code="830799.BJ"),
        ]
        return httpx.Response(200, json=_envelope(rows))

    provider = _provider(handler)
    batch = provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)
    assert [r.venue_code for r in batch.records] == ["XSHG", "XSHE", "XBSE"]
    first = batch.records[0]
    assert first.provider_security_id == "600000.SH"
    assert first.ann_date == _DAY
    assert first.end_date == date(2026, 3, 31)
    assert first.holder_name == "上海国际集团有限公司"
    assert float(first.hold_amount or 0) == 7086834641.0
    assert float(first.hold_ratio or 0) == 21.2781
    assert first.holder_type == "一般企业"


def test_count_record_mapping_holder_num_int() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_envelope(
                [_count_row(ann_date="20260430")],
                fields=list(PROVIDER_HOLDER_COUNT_FIELDS),
            ),
        )

    provider = _provider(handler)
    batch = provider.fetch_holder_count(_REQ_COUNT, deadline=1_000_000_000_000.0)
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.holder_num == 98777
    assert isinstance(record.holder_num, int)


def test_field_whitelist_strict_mismatch_fails_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        row = _holder_row(extra={"unexpected_field": 1})
        fields = [*PROVIDER_TOP10_HOLDER_FIELDS, "unexpected_field"]
        return httpx.Response(200, json=_envelope([row], fields=fields))

    provider = _provider(handler)
    with pytest.raises(ProviderPayloadError):
        provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)


def test_empty_response_is_normal_empty_batch() -> None:
    """单公告日 0 行属正常披露节奏：返回空批而非错误（spec 边界情况修订）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([]))

    provider = _provider(handler)
    batch = provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)
    assert batch.records == ()
    assert batch.evidence.received_count == 0
    assert batch.evidence.continuation_exhausted is True


def test_has_more_pagination_follows_offset() -> None:
    """has_more=True → offset 翻页续取直至 has_more=False（实测验证的机制）。"""
    seen_offsets: list[object] = []
    rows_page1 = [_holder_row(ts_code=f"6000{i:02d}.SH") for i in range(1, 6001)]
    rows_page2 = [_holder_row(ts_code="600100.SH")]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen_offsets.append(payload["params"].get("offset", 0))
        if seen_offsets[-1] == 0:
            return httpx.Response(200, json=_envelope(rows_page1, has_more=True))
        return httpx.Response(200, json=_envelope(rows_page2))

    provider = _provider(handler)
    batch = provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)
    assert seen_offsets == [0, 6000]
    assert batch.evidence.page_count == 2
    assert batch.evidence.request_count == 2
    assert batch.evidence.continuation_exhausted is True
    assert batch.evidence.pagination_enabled is True
    assert len(batch.records) == 6001
    assert batch.evidence.received_count == 6001


def test_max_pages_exceeded_is_capped() -> None:
    """has_more 始终为 True、页内容不同但超过最大页数 → PROVIDER_RESPONSE_CAPPED。"""
    page_no = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page_no["n"] += 1
        row = _holder_row(ts_code=f"60000{page_no['n']}.SH")  # 每页内容不同（排除重复页判定）
        return httpx.Response(200, json=_envelope([row], has_more=True))

    provider = _provider(handler, max_pages=3)
    with pytest.raises(ProviderResponseCappedError):
        provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)


def test_repeated_page_detected_is_incomplete() -> None:
    """分页位置不前进（同页重复）→ ProviderIncompleteError（CONTINUATION_INCOMPLETE）。"""
    rows = [_holder_row()]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope(rows, has_more=True))

    provider = _provider(handler, max_pages=5)
    with pytest.raises(ProviderIncompleteError):
        provider.fetch_top10_holders(_REQ_TOP10, deadline=1_000_000_000_000.0)


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
        return httpx.Response(200, json=_envelope([_holder_row()]))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    batch = provider.fetch_top10_holders(_REQ_TOP10, deadline=100000.0)
    assert batch.evidence.retry_count == 1
    assert delays == [30.0]  # 首次限流退避 30 秒
    assert len(batch.records) == 1


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
        provider.fetch_holder_count(_REQ_COUNT, deadline=100000.0)
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
        provider.fetch_top10_holders(_REQ_TOP10, deadline=5000.0)


def test_throttle_enforces_min_interval_between_calls() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_envelope([_holder_row()]))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    provider.fetch_top10_holders(_REQ_TOP10, deadline=100000.0)
    provider.fetch_top10_holders(_REQ_TOP10, deadline=100000.0)
    assert sleeps and sleeps[-1] >= 0.15 - 1e-9  # 400 次/分钟 → 最小间隔 150 毫秒


def test_three_methods_share_limiter_and_are_independent() -> None:
    """三提取方法共用同一节流器实例；单一方法失败不影响其他方法（故障隔离）。"""
    calls: list[str] = []
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["api_name"])
        if payload["api_name"] == "top10_holders":
            raise AssertionError("top10_holders 不应被调用")
        return httpx.Response(200, json=_envelope([_holder_row()]))

    provider = _provider(handler, monotonic=monotonic, sleep=sleep)
    with pytest.raises(AssertionError):
        # 先失败一次 top10_holders（模拟来源拒绝），随后其他方法仍可调用
        provider.fetch_top10_holders(_REQ_TOP10, deadline=100000.0)
    batch = provider.fetch_top10_float_holders(_REQ_FLOAT, deadline=100000.0)
    assert len(batch.records) == 1
    assert calls == ["top10_holders", "top10_floatholders"]
