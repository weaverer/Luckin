"""Shared FastAPI dependency providers."""

from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from redis import Redis

from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.ports.session_store import SessionStore
from lucking.repositories.redis_session import RedisSessionStore
from lucking.repositories.workbench.users import SqlAlchemyUserRepository
from lucking.services.auth import AuthenticatedSession, AuthService, SessionInvalidError


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_request_id(request: Request) -> str:
    return str(request.state.request_id)


@lru_cache
def _get_session_store() -> RedisSessionStore:
    settings = get_settings()
    password = (
        settings.redis_password.get_secret_value() if settings.redis_password is not None else None
    )
    client = Redis.from_url(
        settings.redis_url,
        password=password,
        decode_responses=True,
    )
    return RedisSessionStore(
        client,
        idle_timeout_seconds=settings.session_idle_timeout_seconds,
        absolute_timeout_seconds=settings.session_absolute_timeout_seconds,
    )


async def get_session_store() -> RedisSessionStore:
    return _get_session_store()


@lru_cache
def _get_auth_service() -> AuthService:
    settings = get_settings()
    engine = create_database_engine(settings)
    users = SqlAlchemyUserRepository(create_session_factory(engine))
    return AuthService(users, _get_session_store())


async def get_auth_service() -> AuthService:
    return _get_auth_service()


async def get_current_session(
    service: Annotated[AuthService, Depends(get_auth_service)],
    session_token: Annotated[str | None, Cookie(alias="lucking_session")] = None,
) -> AuthenticatedSession:
    if not session_token:
        raise ApiError(401, BusinessErrorCode.SESSION_INVALID, "请先登录")
    try:
        return service.authenticate(session_token)
    except SessionInvalidError as exc:
        raise ApiError(401, BusinessErrorCode.SESSION_INVALID, "会话已失效") from exc


async def require_csrf(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(get_current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> AuthenticatedSession:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    same_origin = origin == expected_origin or (
        origin is None and referer is not None and referer.startswith(f"{expected_origin}/")
    )
    if (
        not same_origin
        or csrf_token is None
        or not store.verify_csrf(session.session_token, csrf_token)
    ):
        raise ApiError(403, BusinessErrorCode.CSRF_VALIDATION_FAILED, "请求来源校验失败")
    return session
