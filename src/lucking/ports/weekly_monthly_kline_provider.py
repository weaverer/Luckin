"""供应商无关契约：WeeklyMonthlyKlineProvider（周/月K线，同一接口两个独立模型）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode


class KlineFreq(StrEnum):
    WEEK = "WEEK"
    MONTH = "MONTH"


@dataclass(frozen=True, slots=True)
class KlineRequest:
    freq: KlineFreq
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderWeeklyMonthlyKline:
    freq: KlineFreq
    trade_date: date
    end_date: date | None
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vol: Decimal
    amount: Decimal
    change: Decimal
    pct_chg: Decimal


@dataclass(frozen=True, slots=True)
class ProviderKlineBatch:
    provider_code: str
    freq: KlineFreq
    target_trade_date: date
    records: tuple[ProviderWeeklyMonthlyKline, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@runtime_checkable
class WeeklyMonthlyKlineProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_kline(
        self,
        request: KlineRequest,
        *,
        deadline: float,
    ) -> ProviderKlineBatch: ...
