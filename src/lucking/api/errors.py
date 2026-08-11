"""Stable business errors and FastAPI exception mapping."""

from enum import IntEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lucking.api.responses import ApiResponse, ErrorDetail, utc_timestamp


class BusinessErrorCode(IntEnum):
    INVALID_CREDENTIALS = 100001
    SESSION_INVALID = 100002
    CSRF_VALIDATION_FAILED = 100003
    REQUEST_VALIDATION_FAILED = 200001
    QUERY_RANGE_INVALID = 200002
    PASSWORD_POLICY_VIOLATION = 200003
    RESOURCE_NOT_FOUND = 300001
    IMPORTANT_DATE_CONFLICT = 400001
    WATCHLIST_NAME_CONFLICT = 400002
    WATCHLIST_MEMBER_CONFLICT = 400003
    WATCHLIST_CAPACITY_EXCEEDED = 400004
    RATE_LIMITED = 500001
    EXTERNAL_DEPENDENCY_UNAVAILABLE = 500002
    INTERNAL_SERVER_ERROR = 900001


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: BusinessErrorCode,
        message: str,
        *,
        errors: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.errors = errors or []
        self.headers = headers


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _error_response(
    request: Request,
    status_code: int,
    code: BusinessErrorCode,
    message: str,
    errors: list[ErrorDetail],
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = ApiResponse[None](
        code=int(code),
        message=message,
        data=None,
        errors=errors,
        request_id=_request_id(request),
        timestamp=utc_timestamp(),
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers=headers,
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        exc.errors,
        exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        ErrorDetail(
            field=".".join(str(part) for part in error["loc"] if part not in {"body", "query"})
            or None,
            code=str(error["type"]),
            message="输入值无效",
        )
        for error in exc.errors()
    ]
    return _error_response(
        request,
        400,
        BusinessErrorCode.REQUEST_VALIDATION_FAILED,
        "请求参数校验失败",
        details,
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    del exc
    return _error_response(
        request,
        500,
        BusinessErrorCode.INTERNAL_SERVER_ERROR,
        "服务暂时不可用",
        [],
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
