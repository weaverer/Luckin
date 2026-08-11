"""Provider-neutral notification delivery contract."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class NotificationDisposition(StrEnum):
    DELIVERED = "DELIVERED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    title: str
    text: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class NotificationResult:
    disposition: NotificationDisposition
    provider_code: str
    response_status: int | None = None
    error_category: str | None = None
    error_summary: str | None = None


@runtime_checkable
class NotificationSender(Protocol):
    provider_code: str

    def send(self, message: NotificationMessage) -> NotificationResult: ...
