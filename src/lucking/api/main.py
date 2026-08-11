"""FastAPI application factory and protocol middleware."""

from collections.abc import Awaitable, Callable
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from lucking.api.errors import BusinessErrorCode, install_error_handlers
from lucking.api.responses import ApiResponse, success_response
from lucking.api.routes import api_router
from lucking.config import Settings
from lucking.logging import JsonlLogStore

_ERROR_STATUSES: dict[str, tuple[str, ...]] = {
    "login": ("400", "401", "429", "500"),
    "logout": ("400", "401", "403", "500"),
    "getCurrentUser": ("401", "500"),
    "changePassword": ("400", "401", "403", "500"),
    "listCalendar": ("400", "401", "500"),
    "createImportantDate": ("400", "401", "403", "409", "500"),
    "updateImportantDate": ("400", "401", "403", "404", "409", "500"),
    "deleteImportantDate": ("400", "401", "403", "404", "500"),
    "listStocks": ("400", "401", "500"),
    "getStock": ("400", "401", "404", "500"),
    "listDailyQuotes": ("400", "401", "404", "500"),
    "listWatchlists": ("401", "500"),
    "createWatchlist": ("400", "401", "403", "409", "500"),
    "orderWatchlists": ("400", "401", "403", "404", "500"),
    "updateWatchlist": ("400", "401", "403", "404", "409", "500"),
    "deleteWatchlist": ("400", "401", "403", "404", "500"),
    "addWatchlistMember": ("400", "401", "403", "404", "409", "500"),
    "removeWatchlistMember": ("400", "401", "403", "404", "500"),
    "listBrokerRecommendations": ("400", "401", "500"),
    "getJGoldResearch": ("400", "401", "500"),
    "getJGoldStockResearch": ("400", "401", "404", "500"),
    "listTaskStatus": ("400", "401", "500"),
    "getTaskSummary": ("400", "401", "404", "500"),
}


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()
    app = FastAPI(title="Lucking 投资工作台 API", version="1.0.0")
    app.state.settings = active_settings
    access_log = JsonlLogStore(
        active_settings.trading_calendar_log_dir,
        filename="workbench-api.jsonl",
    )

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        started = monotonic()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = int((monotonic() - started) * 1000)
            access_log.write(
                "api_request_completed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                http_status=response.status_code if response is not None else 500,
                duration_ms=duration_ms,
            )
            if response is not None:
                response.headers["X-Request-ID"] = request_id

    install_error_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False, response_model=ApiResponse[dict[str, str]])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            content=success_response(
                {"status": "ok", "service": "lucking-api"},
                str(request.state.request_id),
            ).model_dump(mode="json")
        )

    def contract_openapi() -> dict[str, object]:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    if isinstance(operation, dict):
                        responses = operation.get("responses", {})
                        responses.pop("422", None)
                        operation_id = operation.get("operationId")
                        for status in _ERROR_STATUSES.get(str(operation_id), ()):
                            responses.setdefault(
                                status,
                                {
                                    "description": "统一错误响应",
                                    "content": {
                                        "application/json": {
                                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                        }
                                    },
                                },
                            )
            components = schema.setdefault("components", {})
            schemas = components.setdefault("schemas", {})
            schemas["BusinessErrorCode"] = {
                "type": "integer",
                "enum": [int(code) for code in BusinessErrorCode],
            }
            schemas["ErrorResponse"] = {
                "type": "object",
                "required": [
                    "code",
                    "message",
                    "data",
                    "errors",
                    "request_id",
                    "timestamp",
                ],
                "properties": {
                    "code": {"$ref": "#/components/schemas/BusinessErrorCode"},
                    "message": {"type": "string"},
                    "data": {"type": "null"},
                    "errors": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ErrorDetail"},
                    },
                    "request_id": {"type": "string", "minLength": 1},
                    "timestamp": {"type": "string", "format": "date-time"},
                },
            }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = contract_openapi  # type: ignore[method-assign]
    return app
