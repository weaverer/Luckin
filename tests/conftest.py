import os
from collections.abc import Iterator
from datetime import UTC, date, datetime

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from lucking.db import Base


@pytest.fixture
def as_of_date() -> date:
    return date(2026, 7, 26)


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 7, 26, 2, 0, tzinfo=UTC)


@pytest.fixture
def sqlite_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def mysql_session_factory() -> Iterator[sessionmaker[Session]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL")
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def transport_factory() -> type[httpx.MockTransport]:
    return httpx.MockTransport
