from dataclasses import fields
from datetime import date, timedelta

import pytest

from lucking.config import Settings
from lucking.integrations import registry
from lucking.ports.trading_calendar_provider import (
    MarketCode,
    ProviderCalendarDay,
    ProviderConfigurationError,
    ProviderRequestError,
    TradingCalendarProvider,
)


class MemoryTradingCalendarProvider:
    provider_code = "memory"

    def fetch_calendar(
        self, market_code: MarketCode, start_date: date, end_date: date
    ) -> list[ProviderCalendarDay]:
        MarketCode.enabled(market_code)
        count = (end_date - start_date).days + 1
        return [
            ProviderCalendarDay(
                market_code=market_code,
                calendar_date=start_date + timedelta(days=offset),
                is_open=(start_date + timedelta(days=offset)).weekday() < 5,
                previous_open_date=None,
                source=self.provider_code,
                source_market="TEST",
            )
            for offset in range(count)
        ]


def test_memory_provider_satisfies_supplier_independent_contract() -> None:
    provider: TradingCalendarProvider = MemoryTradingCalendarProvider()
    rows = provider.fetch_calendar(MarketCode.CN_STOCK, date(2026, 7, 1), date(2026, 7, 3))

    assert provider.provider_code == "memory"
    assert [row.calendar_date for row in rows] == [
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
    ]
    assert {field.name for field in fields(rows[0])} == {
        "market_code",
        "calendar_date",
        "is_open",
        "previous_open_date",
        "source",
        "source_market",
    }


def test_memory_provider_rejects_reserved_market_before_fetch() -> None:
    with pytest.raises(ProviderRequestError):
        MemoryTradingCalendarProvider().fetch_calendar(
            MarketCode.HK_STOCK, date(2026, 7, 1), date(2026, 7, 2)
        )


def test_registry_selects_explicit_provider_without_fallback(monkeypatch) -> None:
    monkeypatch.setitem(
        registry.PROVIDERS,
        "memory",
        lambda _: MemoryTradingCalendarProvider(),
    )
    settings = Settings(
        _env_file=None,
        trading_calendar_provider="memory",
        tushare_token=None,
    )
    provider = registry.build_trading_calendar_provider("memory", settings)
    assert provider.provider_code == "memory"

    with pytest.raises(ProviderConfigurationError) as raised:
        registry.build_trading_calendar_provider("unknown", settings)
    assert "token" not in str(raised.value).lower()


def test_tushare_configuration_is_validated_only_when_selected() -> None:
    settings = Settings(_env_file=None, tushare_token=None)
    with pytest.raises(ProviderConfigurationError) as raised:
        registry.build_trading_calendar_provider("tushare", settings)
    assert "TUSHARE_TOKEN" not in str(raised.value)
