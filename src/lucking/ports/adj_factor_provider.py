"""供应商无关契约：AdjFactorProvider（日线复权因子）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode


@dataclass(frozen=True, slots=True)
class AdjFactorRequest:
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderAdjFactor:
    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    adj_factor: Decimal


@dataclass(frozen=True, slots=True)
class ProviderAdjFactorBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderAdjFactor, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@runtime_checkable
class AdjFactorProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_adj_factors(
        self,
        request: AdjFactorRequest,
        *,
        deadline: float,
    ) -> ProviderAdjFactorBatch: ...
