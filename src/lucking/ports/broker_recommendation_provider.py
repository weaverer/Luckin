"""Provider-neutral monthly broker recommendation contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"


@dataclass(frozen=True, slots=True)
class BrokerRecommendationRequest:
    target_month: date

    def __post_init__(self) -> None:
        if self.target_month.day != 1:
            raise ValueError("target_month 必须是月份第一日")


@dataclass(frozen=True, slots=True)
class ProviderBrokerRecommendation:
    recommendation_month: date
    broker_name: str
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    stock_name: str


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    request_count: int
    completed_request_count: int
    retry_count: int
    page_count: int
    page_limit: int
    last_page_count: int
    received_count: int
    pagination_enabled: bool
    continuation_exhausted: bool
    repeated_page_detected: bool = False


@dataclass(frozen=True, slots=True)
class ProviderBrokerRecommendationBatch:
    provider_code: str
    target_month: date
    records: tuple[ProviderBrokerRecommendation, ...]
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
        request_no: int | None = None,
    ) -> None:
        self.provider_code = provider_code
        self.summary = summary[:500]
        self.status_code = status_code
        self.request_no = request_no
        super().__init__(f"{self.category}: {self.summary}")


class ProviderAuthenticationError(ProviderError):
    category = "AUTHENTICATION"


class ProviderRateLimitedError(ProviderError):
    category = "PROVIDER_RATE_LIMITED"
    retryable = True


class ProviderQuotaExceededError(ProviderError):
    category = "QUOTA_EXCEEDED"


class ProviderUnavailableError(ProviderError):
    category = "PROVIDER_UNAVAILABLE"
    retryable = True


class ProviderRequestError(ProviderError):
    category = "PROVIDER_REQUEST"


class ProviderPayloadError(ProviderError):
    category = "INVALID_FIELD"


class ProviderIncompleteError(ProviderError):
    category = "CONTINUATION_INCOMPLETE"


class ProviderDeadlineExceededError(ProviderError):
    category = "PROVIDER_DEADLINE"


class ProviderConfigurationError(ProviderError):
    category = "PROVIDER_CONFIGURATION"


@runtime_checkable
class BrokerRecommendationProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_month(
        self,
        request: BrokerRecommendationRequest,
        *,
        deadline: float,
    ) -> ProviderBrokerRecommendationBatch: ...
