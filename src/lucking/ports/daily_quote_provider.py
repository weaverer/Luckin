"""供应商无关契约：DailyQuoteProvider（未复权日线行情）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode


@dataclass(frozen=True, slots=True)
class DailyQuoteRequest:
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderDailyQuote:
    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    change: Decimal
    pct_chg: Decimal
    vol: Decimal
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ProviderDailyQuoteBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderDailyQuote, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@runtime_checkable
class DailyQuoteProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_daily_quotes(
        self,
        request: DailyQuoteRequest,
        *,
        deadline: float,
    ) -> ProviderDailyQuoteBatch: ...
