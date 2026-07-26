from datetime import date

import pytest

from lucking.flows.trading_calendar import resolve_sync_window
from lucking.ports.trading_calendar_provider import MarketCode, ProviderRequestError, SyncMode
from lucking.services.trading_calendar import InvalidSyncRequest


@pytest.mark.parametrize(
    ("mode", "as_of", "expected"),
    [
        (SyncMode.MONTHLY, date(2026, 7, 26), (date(2026, 7, 1), date(2026, 12, 31))),
        (SyncMode.YEAR_END, date(2026, 12, 20), (date(2027, 1, 1), date(2027, 12, 31))),
    ],
)
def test_automatic_windows(
    mode: SyncMode, as_of: date, expected: tuple[date, date]
) -> None:
    assert resolve_sync_window(mode, as_of_date=as_of) == expected


def test_manual_window_requires_both_dates() -> None:
    with pytest.raises(InvalidSyncRequest):
        resolve_sync_window(SyncMode.MANUAL, as_of_date=date(2026, 7, 26))


def test_only_cn_stock_market_is_enabled() -> None:
    assert MarketCode.enabled("CN-S") is MarketCode.CN_STOCK
    with pytest.raises(ProviderRequestError):
        MarketCode.enabled("HK-S")
