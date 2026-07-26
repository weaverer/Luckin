import json

import httpx
import pytest

from lucking.integrations.tushare.client import (
    TushareClient,
    TushareError,
    TushareErrorCategory,
)


def _client(handler: httpx.MockTransport) -> TushareClient:
    return TushareClient("super-secret-token", transport=handler)


@pytest.mark.parametrize("api_name", ["trade_cal", "fictional_api"])
def test_generic_client_has_no_endpoint_hardcoding(api_name: str) -> None:
    captured: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": None,
                "data": {"fields": ["value", "key"], "items": [[2, "a"]]},
            },
        )

    table = _client(httpx.MockTransport(handle)).call(
        api_name, params={"x": 1}, fields=("key", "value")
    )
    assert captured["api_name"] == api_name
    assert captured["fields"] == "key,value"
    assert table.rows[0]["key"] == "a"
    with pytest.raises(TypeError):
        table.rows[0]["key"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (httpx.Response(429), TushareErrorCategory.RATE_LIMITED),
        (httpx.Response(503), TushareErrorCategory.UPSTREAM_UNAVAILABLE),
        (
            httpx.Response(200, json={"code": -2001, "msg": "积分不足", "data": None}),
            TushareErrorCategory.QUOTA_EXHAUSTED,
        ),
        (
            httpx.Response(200, json={"code": -2001, "msg": "token无效", "data": None}),
            TushareErrorCategory.AUTHENTICATION,
        ),
    ],
)
def test_error_classification_and_secret_redaction(
    response: httpx.Response, category: TushareErrorCategory
) -> None:
    transport = httpx.MockTransport(lambda _: response)
    with pytest.raises(TushareError) as raised:
        _client(transport).call("anything", params={}, fields=("a",))
    assert raised.value.category is category
    assert "super-secret-token" not in str(raised.value)


def test_network_failure_is_retryable_and_redacted() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("super-secret-token connection failed", request=request)

    with pytest.raises(TushareError) as raised:
        _client(httpx.MockTransport(fail)).call("anything", params={}, fields=("a",))
    assert raised.value.category is TushareErrorCategory.NETWORK
    assert "super-secret-token" not in str(raised.value)


def test_invalid_envelope_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"code": 0, "data": {"fields": ["a"], "items": []}})
    )
    with pytest.raises(TushareError) as raised:
        _client(transport).call("anything", params={}, fields=("a",))
    assert raised.value.category is TushareErrorCategory.EMPTY_PAYLOAD


def test_allow_empty_is_opt_in_and_keeps_default_behavior() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"code": 0, "data": {"fields": ["a"], "items": []}},
        )
    )
    client = _client(transport)
    with pytest.raises(TushareError) as raised:
        client.call("trade_cal", params={}, fields=("a",))
    assert raised.value.category is TushareErrorCategory.EMPTY_PAYLOAD

    table = client.call("stock_basic", params={}, fields=("a",), allow_empty=True)
    assert table.fields == ("a",)
    assert table.rows == ()

