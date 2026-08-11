import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from lucking.api.dependencies import get_auth_service, get_session_store
from lucking.api.main import create_app
from lucking.api.routes import broker_recommendations as broker_routes
from lucking.api.routes import task_status as task_routes
from lucking.api.routes.stocks import quote_dto
from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.ports.task_execution_reader import TaskExecution, TaskExecutionStatus
from lucking.repositories.stock_list import StockListItem
from lucking.repositories.workbench_queries.broker_recommendations import (
    BrokerRecommendationItem,
)
from lucking.services.auth import (
    AuthenticatedSession,
    InvalidCredentialsError,
    LoginRateLimitedError,
)
from lucking.services.daily_task_summary import StoredSummary, SummarySnapshot


class ApiSessionStore:
    def __init__(self) -> None:
        self.valid_csrf = "csrf-token"

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool:
        return session_token == "session-token" and csrf_token == self.valid_csrf

    def rotate_csrf(self, session_token: str) -> str | None:
        return self.valid_csrf if session_token == "session-token" else None


class ApiAuthService:
    def login(self, username: str, password: str, client_address: str):
        if username == "limited":
            raise LoginRateLimitedError("登录尝试过于频繁")
        if password != "correct-password":
            raise InvalidCredentialsError("账号或密码错误")
        return AuthenticatedSession(
            "user-1",
            username.lower(),
            "分析员",
            "session-token",
            "csrf-token",
            datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        )

    def authenticate(self, session_token: str):
        if session_token != "session-token":
            from lucking.services.auth import SessionInvalidError

            raise SessionInvalidError("invalid")
        return AuthenticatedSession(
            "user-1",
            "analyst",
            "分析员",
            session_token,
            "",
            datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        )

    def logout(self, session_token: str) -> None:
        assert session_token == "session-token"

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        assert user_id == "user-1"


async def api_request(method: str, path: str, **kwargs):
    app = create_app()

    async def auth_override():
        return ApiAuthService()

    async def session_override():
        return ApiSessionStore()

    app.dependency_overrides[get_auth_service] = auth_override
    app.dependency_overrides[get_session_store] = session_override
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_login_cookie_and_uniform_failure() -> None:
    success = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "correct-password"},
        )
    )
    assert success.status_code == 200
    assert "HttpOnly" in success.headers["set-cookie"]
    assert success.json()["code"] == 0

    failure = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "wrong-password"},
        )
    )
    assert failure.status_code == 401
    assert failure.json()["code"] == 100001
    assert set(failure.json()) == {
        "code",
        "message",
        "data",
        "errors",
        "request_id",
        "timestamp",
    }


def test_write_requires_same_origin_and_csrf() -> None:
    cookies = {"lucking_session": "session-token"}
    rejected = asyncio.run(api_request("POST", "/api/v1/auth/logout", cookies=cookies))
    assert rejected.status_code == 403
    assert rejected.json()["code"] == 100003

    accepted = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/logout",
            cookies=cookies,
            headers={"Origin": "http://test", "X-CSRF-Token": "csrf-token"},
        )
    )
    assert accepted.status_code == 204
    assert accepted.content == b""


def test_login_rate_limit_uses_429_and_retry_after() -> None:
    response = asyncio.run(
        api_request(
            "POST",
            "/api/v1/auth/login",
            json={"username": "limited", "password": "correct-password"},
        )
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert response.json()["code"] == 500001


def test_broker_recommendations_are_nested_and_paginated(monkeypatch) -> None:
    stock = StockListItem(
        "stock-1",
        "CN-S",
        VenueCode.SHANGHAI,
        "600000",
        "浦发银行",
        "CNY",
        ListingStatus.ACTIVE,
        None,
        None,
    )
    item = BrokerRecommendationItem(
        "recommendation-1",
        date(2026, 8, 1),
        "中信证券",
        stock,
        datetime(2026, 8, 8, 8),
    )
    service = SimpleNamespace(
        list=lambda *args: SimpleNamespace(items=[item], total=1, limit=args[3], offset=args[4])
    )
    monkeypatch.setattr(broker_routes, "query_service", lambda: service)
    response = asyncio.run(
        api_request(
            "GET",
            "/api/v1/broker-recommendations?recommendation_month=2026-08-01",
            cookies={"lucking_session": "session-token"},
        )
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["pagination"]["total"] == 1
    assert body["data"]["items"][0]["stock"]["security_code"] == "600000"
    assert "provider_code" not in str(body)


def test_quote_dto_ignores_clickhouse_identity_fields() -> None:
    quote = quote_dto(
        {
            "stock_id": "stock-1",
            "venue_code": "XBSE",
            "security_code": "920000",
            "trade_date": "2026-08-08",
            "open": "14.10",
            "high": "14.50",
            "low": "14.00",
            "close": "14.32",
            "pre_close": "14.08",
            "change": "0.24",
            "pct_chg": "1.70",
            "vol": "20079",
            "amount": "287456.88",
            "updated_at": "2026-08-08 11:00:00.000",
        }
    )

    assert quote.close == "14.32"
    assert "stock_id" not in quote.model_dump()


def task_snapshot() -> SummarySnapshot:
    observed = datetime(2026, 8, 8, 12, tzinfo=UTC)
    executions = tuple(
        TaskExecution(
            task_key=status.value.lower(),
            schedule_slug=status.value.lower(),
            display_name=status.value,
            source_domain="test",
            business_date=date(2026, 8, 8),
            status=status,
            observed_at=observed,
            error_summary="安全错误摘要" if status is TaskExecutionStatus.FAILED else None,
        )
        for status in TaskExecutionStatus
    )
    return SummarySnapshot(
        date(2026, 8, 8),
        observed,
        executions,
        {status: 1 for status in TaskExecutionStatus},
        "a" * 64,
    )


def test_live_task_six_states_and_historical_summary_404(monkeypatch) -> None:
    snapshot = task_snapshot()
    service = SimpleNamespace(live=lambda *_: snapshot, history=lambda *_: None)
    monkeypatch.setattr(task_routes, "query_service", lambda: service)
    live = asyncio.run(
        api_request(
            "GET",
            "/api/v1/task-status?business_date=2026-08-08",
            cookies={"lucking_session": "session-token"},
        )
    )
    assert live.status_code == 200
    assert {item["status"] for item in live.json()["data"]["items"]} == {
        status.value for status in TaskExecutionStatus
    }
    assert "安全错误摘要" in str(live.json())

    missing = asyncio.run(
        api_request(
            "GET",
            "/api/v1/task-summaries/2026-08-08",
            cookies={"lucking_session": "session-token"},
        )
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == 300001


def test_historical_summary_exposes_notification_status(monkeypatch) -> None:
    snapshot = task_snapshot()
    stored = StoredSummary(
        "summary-1",
        "FAILED",
        snapshot,
        generated_at=datetime(2026, 8, 8, 12, 1, tzinfo=UTC),
    )
    service = SimpleNamespace(history=lambda *_: stored)
    monkeypatch.setattr(task_routes, "query_service", lambda: service)
    response = asyncio.run(
        api_request(
            "GET",
            "/api/v1/task-summaries/2026-08-08",
            cookies={"lucking_session": "session-token"},
        )
    )
    assert response.status_code == 200
    assert response.json()["data"]["notification_status"] == "FAILED"
    assert response.json()["data"]["counts"]["FAILED"] == 1
    assert "latest_notification_attempt" in response.json()["data"]
