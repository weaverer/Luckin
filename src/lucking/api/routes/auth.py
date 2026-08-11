"""Cookie-session authentication routes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from lucking.api.dependencies import (
    get_auth_service,
    get_current_session,
    get_request_id,
    get_session_store,
    require_csrf,
)
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, success_response
from lucking.config import Settings
from lucking.ports.session_store import SessionStore
from lucking.services.auth import (
    AuthenticatedSession,
    AuthService,
    InvalidCredentialsError,
    LoginRateLimitedError,
    PasswordPolicyError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class UserData(BaseModel):
    user_id: str
    username: str
    display_name: str


class AuthSessionData(BaseModel):
    user: UserData
    csrf_token: str
    expires_at: datetime


def _session_data(session: AuthenticatedSession, csrf_token: str) -> AuthSessionData:
    return AuthSessionData(
        user=UserData(
            user_id=session.user_id,
            username=session.username,
            display_name=session.display_name,
        ),
        csrf_token=csrf_token,
        expires_at=session.expires_at,
    )


@router.post("/login", response_model=ApiResponse[AuthSessionData], operation_id="login")
async def login(
    command: LoginRequest,
    request: Request,
    response: Response,
    request_id: Annotated[str, Depends(get_request_id)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> ApiResponse[AuthSessionData]:
    try:
        session = service.login(
            command.username,
            command.password,
            request.client.host if request.client is not None else "unknown",
        )
    except InvalidCredentialsError as exc:
        raise ApiError(401, BusinessErrorCode.INVALID_CREDENTIALS, "账号或密码错误") from exc
    except LoginRateLimitedError as exc:
        raise ApiError(
            429,
            BusinessErrorCode.RATE_LIMITED,
            "登录尝试过于频繁",
            headers={"Retry-After": "300"},
        ) from exc
    settings: Settings = request.app.state.settings
    response.set_cookie(
        settings.session_cookie_name,
        session.session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path=settings.session_cookie_path,
        max_age=settings.session_absolute_timeout_seconds,
    )
    return success_response(_session_data(session, session.csrf_token), request_id)


@router.get("/me", response_model=ApiResponse[AuthSessionData], operation_id="getCurrentUser")
async def current_user(
    session: Annotated[AuthenticatedSession, Depends(get_current_session)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    request_id: Annotated[str, Depends(get_request_id)],
) -> ApiResponse[AuthSessionData]:
    csrf_token = store.rotate_csrf(session.session_token)
    if csrf_token is None:
        raise ApiError(401, BusinessErrorCode.SESSION_INVALID, "会话已失效")
    return success_response(_session_data(session, csrf_token), request_id)


@router.post("/logout", status_code=204, operation_id="logout")
async def logout(
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(require_csrf)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    service.logout(session.session_token)
    response.delete_cookie("lucking_session", path="/")


@router.put("/password", status_code=204, operation_id="changePassword")
async def change_password(
    command: ChangePasswordRequest,
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(require_csrf)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    try:
        service.change_password(session.user_id, command.current_password, command.new_password)
    except InvalidCredentialsError as exc:
        raise ApiError(401, BusinessErrorCode.INVALID_CREDENTIALS, "当前密码错误") from exc
    except PasswordPolicyError as exc:
        raise ApiError(400, BusinessErrorCode.PASSWORD_POLICY_VIOLATION, str(exc)) from exc
    response.delete_cookie("lucking_session", path="/")
