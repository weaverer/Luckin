"""Supplier-independent contract for revocable browser sessions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    session_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: str
    csrf_token_hash: str
    issued_at: datetime
    last_seen_at: datetime
    absolute_expires_at: datetime


class SessionStore(Protocol):
    def create(self, user_id: str) -> SessionCredentials: ...

    def get(self, session_token: str) -> SessionRecord | None: ...

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool: ...

    def rotate_csrf(self, session_token: str) -> str | None: ...

    def delete(self, session_token: str) -> None: ...

    def revoke_user(self, user_id: str) -> None: ...
