"""Provider-neutral stock-list port and canonical data semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ScopeCode(StrEnum):
    CN_STOCK = "CN-S"


class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"


FIXED_VENUES = (
    VenueCode.SHANGHAI,
    VenueCode.SHENZHEN,
    VenueCode.BEIJING,
)


class ListingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"
    SUSPENDED = "SUSPENDED"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class StockListRequest:
    scope_code: ScopeCode = ScopeCode.CN_STOCK


@dataclass(frozen=True, slots=True)
class ProviderStockRecord:
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    display_name: str
    currency_code: str
    listing_status: ListingStatus
    listed_on: date | None
    delisted_on: date | None


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    segment_count: int
    completed_segment_count: int
    capped_segment_count: int
    received_count: int


@dataclass(frozen=True, slots=True)
class ProviderStockList:
    provider_code: str
    scope_code: ScopeCode
    records: tuple[ProviderStockRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime


class ProviderError(RuntimeError):
    category = "PROVIDER_ERROR"
    retryable = False

    def __init__(
        self,
        provider_code: str,
        summary: str,
        *,
        status_code: int | None = None,
        segment_no: int | None = None,
    ) -> None:
        self.provider_code = provider_code
        self.summary = summary[:500]
        self.status_code = status_code
        self.segment_no = segment_no
        super().__init__(f"{self.category}: {self.summary}")


class ProviderAuthenticationError(ProviderError):
    category = "AUTHENTICATION"


class ProviderRateLimitedError(ProviderError):
    category = "RATE_LIMITED"
    retryable = True


class ProviderQuotaExceededError(ProviderError):
    category = "QUOTA_EXCEEDED"


class ProviderUnavailableError(ProviderError):
    category = "UNAVAILABLE"
    retryable = True


class ProviderRequestError(ProviderError):
    category = "REQUEST"


class ProviderPayloadError(ProviderError):
    category = "PAYLOAD"


class ProviderIncompleteError(ProviderError):
    category = "INCOMPLETE"


class ProviderDeadlineExceededError(ProviderError):
    category = "DEADLINE_EXCEEDED"


class ProviderConfigurationError(ProviderError):
    category = "CONFIGURATION"


@runtime_checkable
class StockListProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_stock_list(
        self,
        request: StockListRequest,
        *,
        deadline: float,
    ) -> ProviderStockList: ...

