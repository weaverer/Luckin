"""行情数据 Provider 共享统一异常分类（供应商无关）。"""

from __future__ import annotations


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


class ProviderEmptyAggregateError(ProviderError):
    category = "EMPTY_AGGREGATE"


class ProviderResponseCappedError(ProviderError):
    category = "RESPONSE_CAPPED"


class ProviderIncompleteError(ProviderError):
    category = "CONTINUATION_INCOMPLETE"


class ProviderDeadlineExceededError(ProviderError):
    category = "PROVIDER_DEADLINE"


class ProviderConfigurationError(ProviderError):
    category = "PROVIDER_CONFIGURATION"
