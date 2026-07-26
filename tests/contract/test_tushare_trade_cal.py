from datetime import date

import httpx
import pytest

from lucking.integrations.tushare.client import TushareClient
from lucking.integrations.tushare.trading_calendar_provider import (
    TushareTradingCalendarProvider,
)
from lucking.ports.trading_calendar_provider import (
    MarketCode,
    ProviderPayloadError,
    ProviderQuotaExceededError,
)


def _provider(items: list[list[object]], fields: list[str] | None = None):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": None,
                "data": {
                    "fields": fields
                    or ["exchange", "cal_date", "is_open", "pretrade_date"],
                    "items": items,
                },
            },
        )

    return TushareTradingCalendarProvider(
        TushareClient("secret", transport=httpx.MockTransport(handler))
    )


def test_cn_stock_maps_to_sse_and_standard_dto() -> None:
    rows = _provider(
        [["SSE", "20260701", 1, "20260630"], ["SSE", "20260702", 0, "20260630"]]
    ).fetch_calendar(MarketCode.CN_STOCK, date(2026, 7, 1), date(2026, 7, 3))

    assert [row.is_open for row in rows] == [True, False]
    assert rows[0].market_code is MarketCode.CN_STOCK
    assert rows[0].source == "tushare"
    assert rows[0].source_market == "SSE"
    assert rows[-1].calendar_date == date(2026, 7, 2)  # 不填充未来尾部


@pytest.mark.parametrize(
    "items",
    [
        [["SZSE", "20260701", 1, "20260630"]],
        [["SSE", "bad", 1, "20260630"]],
        [["SSE", "20260701", 2, "20260630"]],
        [["SSE", "20260701", 1, "20260630"], ["SSE", "20260701", 0, "20260630"]],
    ],
)
def test_invalid_trade_calendar_rows_are_rejected(items: list[list[object]]) -> None:
    with pytest.raises(ProviderPayloadError):
        _provider(items).fetch_calendar(
            MarketCode.CN_STOCK, date(2026, 7, 1), date(2026, 7, 2)
        )


def test_quota_error_maps_to_supplier_independent_exception() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"code": -2001, "msg": "当日调用额度不足", "data": None}
        )
    )
    provider = TushareTradingCalendarProvider(TushareClient("secret", transport=transport))
    with pytest.raises(ProviderQuotaExceededError):
        provider.fetch_calendar(MarketCode.CN_STOCK, date(2026, 7, 1), date(2026, 7, 2))

