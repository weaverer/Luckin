"""User-owned important date persistence."""

from datetime import date
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.workbench import AppUser, ImportantDate
from lucking.services.calendar_workspace import normalize_important_date_title


class ImportantDateConflict(ValueError):
    pass


class ImportantDateNotFound(LookupError):
    pass


class SqlAlchemyImportantDateRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def _owner(self, session: Session, user_id: str) -> int:
        value = session.scalar(select(AppUser.id).where(AppUser.user_id == user_id))
        if value is None:
            raise ImportantDateNotFound("用户不存在")
        return value

    def list_range(self, user_id: str, start_date: date, end_date: date) -> list[ImportantDate]:
        with self._sessions() as session:
            owner = self._owner(session, user_id)
            rows = session.scalars(
                select(ImportantDate)
                .where(
                    ImportantDate.user_id == owner,
                    ImportantDate.event_date.between(start_date, end_date),
                )
                .order_by(ImportantDate.event_date, ImportantDate.title_key)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def create(
        self, user_id: str, event_date: date, title: str, notes: str | None
    ) -> ImportantDate:
        clean, title_key = normalize_important_date_title(title)
        try:
            with self._sessions.begin() as session:
                row = ImportantDate(
                    important_date_id=str(uuid4()),
                    user_id=self._owner(session, user_id),
                    event_date=event_date,
                    title=clean,
                    title_key=title_key,
                    notes=notes,
                )
                session.add(row)
                session.flush()
                session.refresh(row)
                session.expunge(row)
                return row
        except IntegrityError as exc:
            raise ImportantDateConflict("重要日已存在") from exc

    def update(
        self, user_id: str, item_id: str, event_date: date, title: str, notes: str | None
    ) -> ImportantDate:
        try:
            with self._sessions.begin() as session:
                owner = self._owner(session, user_id)
                row = session.scalar(
                    select(ImportantDate).where(
                        ImportantDate.important_date_id == item_id, ImportantDate.user_id == owner
                    )
                )
                if row is None:
                    raise ImportantDateNotFound("重要日不存在")
                clean, title_key = normalize_important_date_title(title)
                row.event_date = event_date
                row.title = clean
                row.title_key = title_key
                row.notes = notes
                session.flush()
                session.refresh(row)
                session.expunge(row)
                return row
        except IntegrityError as exc:
            raise ImportantDateConflict("重要日已存在") from exc

    def delete(self, user_id: str, item_id: str) -> None:
        with self._sessions.begin() as session:
            owner = self._owner(session, user_id)
            row = session.scalar(
                select(ImportantDate).where(
                    ImportantDate.important_date_id == item_id, ImportantDate.user_id == owner
                )
            )
            if row is None:
                raise ImportantDateNotFound("重要日不存在")
            session.delete(row)
