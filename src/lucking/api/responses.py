"""Constitution-compliant public API response models."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str | None
    code: str
    message: str


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool


class PageData[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[T]
    pagination: Pagination


class ApiResponse[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: T | None
    errors: list[ErrorDetail]
    request_id: str = Field(min_length=1)
    timestamp: datetime


def utc_timestamp() -> datetime:
    return datetime.now(UTC)


def success_response[T](data: T, request_id: str) -> ApiResponse[T]:
    return ApiResponse[T](
        code=0,
        message="",
        data=data,
        errors=[],
        request_id=request_id,
        timestamp=utc_timestamp(),
    )
