"""Supplier-independent trading-calendar provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol, runtime_checkable


class MarketCode(StrEnum):
    CN_STOCK = "CN-S"
    HK_STOCK = "HK-S"
    JP_STOCK = "JP-S"
    US_STOCK = "US-S"
    KR_STOCK = "KR-S"

    @classmethod
    def enabled(cls, value: str | MarketCode) -> MarketCode:
        try:
            market = cls(value)
        except ValueError as exc:
            raise ProviderRequestError("unknown", "不支持的市场代码") from exc
        if market is not cls.CN_STOCK:
            raise ProviderRequestError("unknown", "该市场尚未启用")
        return market


class SyncMode(StrEnum):
    MONTHLY = "monthly"
    YEAR_END = "year_end"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ProviderCalendarDay:
    market_code: MarketCode
    calendar_date: date
    is_open: bool
    previous_open_date: date | None
    source: str
    source_market: str


class ProviderError(RuntimeError):
    category = "PROVIDER"
    retryable = False

    def __init__(self, provider_code: str, summary: str, status_code: int | None = None) -> None:
        self.provider_code = provider_code
        self.summary = summary[:240]
        self.status_code = status_code
        super().__init__(f"{provider_code}: {self.summary}")


class ProviderAuthenticationError(ProviderError):
    category = "AUTHENTICATION"


class ProviderRateLimitedError(ProviderError):
    category = "RATE_LIMITED"
    retryable = True


class ProviderQuotaExceededError(ProviderError):
    category = "QUOTA_EXHAUSTED"


class ProviderUnavailableError(ProviderError):
    category = "UPSTREAM_UNAVAILABLE"
    retryable = True


class ProviderRequestError(ProviderError):
    category = "BAD_REQUEST"


class ProviderPayloadError(ProviderError):
    category = "INVALID_PAYLOAD"


class ProviderConfigurationError(ProviderError):
    category = "CONFIGURATION"


@runtime_checkable
class TradingCalendarProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_calendar(
        self,
        market_code: MarketCode,
        start_date: date,
        end_date: date,
    ) -> list[ProviderCalendarDay]: ...
