"""Redis-backed opaque session storage."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis

from lucking.ports.session_store import SessionCredentials, SessionRecord


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class RedisSessionStore:
    def __init__(
        self,
        client: Redis,
        *,
        idle_timeout_seconds: int = 1800,
        absolute_timeout_seconds: int = 28800,
        now: Any = None,
    ) -> None:
        self._client = client
        self._idle_timeout = idle_timeout_seconds
        self._absolute_timeout = absolute_timeout_seconds
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def _session_key(token_digest: str) -> str:
        return f"auth:session:{token_digest}"

    @staticmethod
    def _user_key(user_id: str) -> str:
        return f"auth:user:{user_id}:sessions"

    def create(self, user_id: str) -> SessionCredentials:
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        token_digest = _digest(session_token)
        issued_at = self._now()
        expires_at = issued_at + timedelta(seconds=self._absolute_timeout)
        payload = {
            "user_id": user_id,
            "csrf_token_hash": _digest(csrf_token),
            "issued_at": _iso(issued_at),
            "last_seen_at": _iso(issued_at),
            "absolute_expires_at": _iso(expires_at),
        }
        pipeline = self._client.pipeline(transaction=True)
        pipeline.setex(
            self._session_key(token_digest),
            min(self._idle_timeout, self._absolute_timeout),
            json.dumps(payload, separators=(",", ":")),
        )
        pipeline.sadd(self._user_key(user_id), token_digest)
        pipeline.expire(self._user_key(user_id), self._absolute_timeout)
        pipeline.execute()
        return SessionCredentials(session_token, csrf_token, expires_at)

    def get(self, session_token: str) -> SessionRecord | None:
        token_digest = _digest(session_token)
        key = self._session_key(token_digest)
        raw = self._client.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        record = SessionRecord(
            user_id=data["user_id"],
            csrf_token_hash=data["csrf_token_hash"],
            issued_at=_datetime(data["issued_at"]),
            last_seen_at=_datetime(data["last_seen_at"]),
            absolute_expires_at=_datetime(data["absolute_expires_at"]),
        )
        now = self._now()
        if now >= record.absolute_expires_at:
            self.delete(session_token)
            return None
        remaining = int((record.absolute_expires_at - now).total_seconds())
        data["last_seen_at"] = _iso(now)
        self._client.setex(
            key,
            max(1, min(self._idle_timeout, remaining)),
            json.dumps(data, separators=(",", ":")),
        )
        return SessionRecord(
            record.user_id,
            record.csrf_token_hash,
            record.issued_at,
            now,
            record.absolute_expires_at,
        )

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool:
        record = self.get(session_token)
        return record is not None and secrets.compare_digest(
            record.csrf_token_hash, _digest(csrf_token)
        )

    def rotate_csrf(self, session_token: str) -> str | None:
        token_digest = _digest(session_token)
        key = self._session_key(token_digest)
        raw = self._client.get(key)
        if raw is None:
            return None
        csrf_token = secrets.token_urlsafe(32)
        data = json.loads(raw)
        data["csrf_token_hash"] = _digest(csrf_token)
        remaining = self._client.ttl(key)
        if remaining <= 0:
            return None
        self._client.setex(key, remaining, json.dumps(data, separators=(",", ":")))
        return csrf_token

    def delete(self, session_token: str) -> None:
        token_digest = _digest(session_token)
        key = self._session_key(token_digest)
        raw = self._client.get(key)
        pipeline = self._client.pipeline(transaction=True)
        pipeline.delete(key)
        if raw is not None:
            user_id = json.loads(raw)["user_id"]
            pipeline.srem(self._user_key(user_id), token_digest)
        pipeline.execute()

    def revoke_user(self, user_id: str) -> None:
        user_key = self._user_key(user_id)
        digests = self._client.smembers(user_key)
        pipeline = self._client.pipeline(transaction=True)
        for digest in digests:
            pipeline.delete(self._session_key(str(digest)))
        pipeline.delete(user_key)
        pipeline.execute()
