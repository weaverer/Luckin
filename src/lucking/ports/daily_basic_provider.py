"""供应商无关契约：DailyBasicProvider（每日基本面指标）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode


@dataclass(frozen=True, slots=True)
class DailyBasicRequest:
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderDailyBasic:
    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    pe: Decimal | None
    pe_ttm: Decimal | None
    pb: Decimal | None
    ps: Decimal | None
    ps_ttm: Decimal | None
    dv_ratio: Decimal | None
    dv_ttm: Decimal | None
    total_share: Decimal | None
    float_share: Decimal | None
    free_share: Decimal | None
    total_mv: Decimal | None
    circ_mv: Decimal | None
    turnover_rate: Decimal | None
    turnover_rate_f: Decimal | None
    volume_ratio: Decimal | None
    limit_status: int | None


@dataclass(frozen=True, slots=True)
class ProviderDailyBasicBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderDailyBasic, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@runtime_checkable
class DailyBasicProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_daily_basics(
        self,
        request: DailyBasicRequest,
        *,
        deadline: float,
    ) -> ProviderDailyBasicBatch: ...
