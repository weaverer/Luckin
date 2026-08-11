"""Authentication domain service."""

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pwdlib import PasswordHash

from lucking.models.workbench import AppUser, AppUserStatus
from lucking.ports.session_store import SessionStore


class InvalidCredentialsError(ValueError):
    pass


class SessionInvalidError(ValueError):
    pass


class PasswordPolicyError(ValueError):
    pass


class LoginRateLimitedError(ValueError):
    pass


class UserRepository(Protocol):
    def get_by_username(self, username: str) -> AppUser | None: ...

    def get_by_user_id(self, user_id: str) -> AppUser | None: ...

    def record_successful_login(self, user_id: str, at: datetime) -> None: ...

    def change_password(self, user_id: str, password_hash: str, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user_id: str
    username: str
    display_name: str
    session_token: str
    csrf_token: str
    expires_at: datetime


class LoginRateLimiter:
    def __init__(
        self,
        *,
        maximum_attempts: int = 5,
        window: timedelta = timedelta(minutes=5),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._maximum_attempts = maximum_attempts
        self._window = window
        self._now = now or (lambda: datetime.now(UTC))
        self._attempts: dict[str, deque[datetime]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = self._now()
        attempts = self._attempts[key]
        while attempts and now - attempts[0] >= self._window:
            attempts.popleft()
        if len(attempts) >= self._maximum_attempts:
            raise LoginRateLimitedError("登录尝试过于频繁")

    def failed(self, key: str) -> None:
        self._attempts[key].append(self._now())

    def succeeded(self, key: str) -> None:
        self._attempts.pop(key, None)


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionStore,
        *,
        password_hash: PasswordHash | None = None,
        rate_limiter: LoginRateLimiter | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._password_hash = password_hash or PasswordHash.recommended()
        self._rate_limiter = rate_limiter or LoginRateLimiter()
        self._now = now or (lambda: datetime.now(UTC))

    def login(
        self, username: str, password: str, client_address: str = "unknown"
    ) -> AuthenticatedSession:
        normalized = username.strip().lower()
        rate_key = f"{normalized}:{client_address}"
        self._rate_limiter.check(rate_key)
        user = self._users.get_by_username(normalized)
        valid = (
            user is not None
            and user.status == AppUserStatus.ACTIVE
            and self._password_hash.verify(password, user.password_hash)
        )
        if not valid or user is None:
            self._rate_limiter.failed(rate_key)
            raise InvalidCredentialsError("账号或密码错误")
        self._rate_limiter.succeeded(rate_key)
        self._users.record_successful_login(user.user_id, self._now())
        credentials = self._sessions.create(user.user_id)
        return AuthenticatedSession(
            user.user_id,
            user.username,
            user.display_name,
            credentials.session_token,
            credentials.csrf_token,
            credentials.expires_at,
        )

    def authenticate(self, session_token: str) -> AuthenticatedSession:
        record = self._sessions.get(session_token)
        if record is None:
            raise SessionInvalidError("会话已失效")
        user = self._users.get_by_user_id(record.user_id)
        if user is None or user.status != AppUserStatus.ACTIVE:
            self._sessions.delete(session_token)
            raise SessionInvalidError("会话已失效")
        return AuthenticatedSession(
            user.user_id,
            user.username,
            user.display_name,
            session_token,
            "",
            record.absolute_expires_at,
        )

    def logout(self, session_token: str) -> None:
        self._sessions.delete(session_token)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = self._users.get_by_user_id(user_id)
        if user is None or not self._password_hash.verify(current_password, user.password_hash):
            raise InvalidCredentialsError("当前密码错误")
        if not 12 <= len(new_password) <= 128:
            raise PasswordPolicyError("新密码长度必须为 12 至 128 个字符")
        if self._password_hash.verify(new_password, user.password_hash):
            raise PasswordPolicyError("新密码不能与当前密码相同")
        self._users.change_password(user_id, self._password_hash.hash(new_password), self._now())
        self._sessions.revoke_user(user_id)
