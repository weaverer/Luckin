from datetime import UTC, datetime

import pytest

from lucking.models.workbench import AppUserStatus
from lucking.services.auth import (
    AuthService,
    InvalidCredentialsError,
    PasswordPolicyError,
)


class FakeUser:
    def __init__(self, password_hash: str, status: str = AppUserStatus.ACTIVE) -> None:
        self.user_id = "11111111-1111-4111-8111-111111111111"
        self.username = "analyst"
        self.display_name = "分析员"
        self.password_hash = password_hash
        self.status = status


class FakeUserRepository:
    def __init__(self, user: FakeUser | None) -> None:
        self.user = user
        self.changed_hash: str | None = None

    def get_by_username(self, username: str) -> FakeUser | None:
        if self.user is not None and username == self.user.username:
            return self.user
        return None

    def get_by_user_id(self, user_id: str) -> FakeUser | None:
        if self.user is not None and user_id == self.user.user_id:
            return self.user
        return None

    def record_successful_login(self, user_id: str, at: datetime) -> None:
        assert self.user is not None and user_id == self.user.user_id
        assert at.tzinfo is UTC

    def change_password(self, user_id: str, password_hash: str, at: datetime) -> None:
        assert self.user is not None and user_id == self.user.user_id
        self.changed_hash = password_hash
        self.user.password_hash = password_hash


class FakeSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.revoked_users: list[str] = []

    def create(self, user_id: str):
        from lucking.ports.session_store import SessionCredentials

        self.sessions["session-token"] = user_id
        return SessionCredentials(
            session_token="session-token",
            csrf_token="csrf-token",
            expires_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        )

    def get(self, session_token: str):
        from lucking.ports.session_store import SessionRecord

        user_id = self.sessions.get(session_token)
        if user_id is None:
            return None
        now = datetime(2026, 8, 8, 4, 0, tzinfo=UTC)
        return SessionRecord(user_id, "csrf-hash", now, now, now)

    def delete(self, session_token: str) -> None:
        self.sessions.pop(session_token, None)

    def revoke_user(self, user_id: str) -> None:
        self.revoked_users.append(user_id)
        self.sessions = {token: owner for token, owner in self.sessions.items() if owner != user_id}


def build_service(status: str = AppUserStatus.ACTIVE):
    from pwdlib import PasswordHash

    hasher = PasswordHash.recommended()
    repository = FakeUserRepository(FakeUser(hasher.hash("correct-password"), status))
    sessions = FakeSessionStore()
    return AuthService(repository, sessions, password_hash=hasher), repository, sessions


@pytest.mark.parametrize(
    ("username", "password", "status"),
    [
        ("missing", "correct-password", AppUserStatus.ACTIVE),
        ("analyst", "wrong-password", AppUserStatus.ACTIVE),
        ("analyst", "correct-password", AppUserStatus.DISABLED),
    ],
)
def test_login_failures_are_indistinguishable(username: str, password: str, status: str) -> None:
    service, _, _ = build_service(status)

    with pytest.raises(InvalidCredentialsError, match="账号或密码错误"):
        service.login(username, password)


def test_login_normalizes_username_and_creates_session() -> None:
    service, _, _ = build_service()

    authenticated = service.login("  ANALYST ", "correct-password")

    assert authenticated.username == "analyst"
    assert authenticated.session_token == "session-token"
    assert authenticated.csrf_token == "csrf-token"


def test_logout_deletes_current_session() -> None:
    service, _, sessions = build_service()
    service.login("analyst", "correct-password")

    service.logout("session-token")

    assert sessions.sessions == {}


def test_change_password_enforces_policy_and_revokes_all_sessions() -> None:
    service, repository, sessions = build_service()
    service.login("analyst", "correct-password")

    with pytest.raises(PasswordPolicyError):
        service.change_password(repository.user.user_id, "correct-password", "short")  # type: ignore[union-attr]
    with pytest.raises(PasswordPolicyError):
        service.change_password(
            repository.user.user_id,  # type: ignore[union-attr]
            "correct-password",
            "correct-password",
        )

    service.change_password(
        repository.user.user_id,  # type: ignore[union-attr]
        "correct-password",
        "a-new-password-value",
    )

    assert repository.changed_hash is not None
    assert sessions.revoked_users == [repository.user.user_id]  # type: ignore[union-attr]
