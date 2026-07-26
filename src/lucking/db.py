"""SQLAlchemy setup and UTC conversion helpers."""

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from lucking.config import Settings


class Base(DeclarativeBase):
    """Declarative model base."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc_naive(value: datetime) -> datetime:
    """Convert an aware timestamp to MySQL-compatible UTC naive form."""
    if value.tzinfo is None:
        raise ValueError("时间戳必须包含时区")
    return value.astimezone(UTC).replace(tzinfo=None)


def as_utc_aware(value: datetime) -> datetime:
    """Interpret a database timestamp as UTC."""
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()

