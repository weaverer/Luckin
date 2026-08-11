"""AppUser transaction repository."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.workbench import AppUser, AppUserStatus


class SqlAlchemyUserRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_by_username(self, username: str) -> AppUser | None:
        with self._session_factory() as session:
            return session.scalar(select(AppUser).where(AppUser.username == username))

    def get_by_user_id(self, user_id: str) -> AppUser | None:
        with self._session_factory() as session:
            return session.scalar(select(AppUser).where(AppUser.user_id == user_id))

    def create(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        at: datetime,
    ) -> AppUser:
        with self._session_factory.begin() as session:
            user = AppUser(
                user_id=str(uuid4()),
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                status=AppUserStatus.ACTIVE,
                password_changed_at=at,
            )
            session.add(user)
            session.flush()
            session.expunge(user)
            return user

    def disable(self, username: str) -> bool:
        with self._session_factory.begin() as session:
            user = session.scalar(select(AppUser).where(AppUser.username == username))
            if user is None:
                return False
            user.status = AppUserStatus.DISABLED
            return True

    def record_successful_login(self, user_id: str, at: datetime) -> None:
        with self._session_factory.begin() as session:
            user = session.scalar(select(AppUser).where(AppUser.user_id == user_id))
            if user is not None:
                user.last_login_at = at

    def change_password(self, user_id: str, password_hash: str, at: datetime) -> None:
        with self._session_factory.begin() as session:
            user = session.scalar(select(AppUser).where(AppUser.user_id == user_id))
            if user is None:
                raise LookupError("用户不存在")
            user.password_hash = password_hash
            user.password_changed_at = at
