from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.models.workbench import AppUser, AppUserStatus, ImportantDate
from lucking.repositories.workbench.important_dates import (
    ImportantDateConflict,
    ImportantDateNotFound,
    SqlAlchemyImportantDateRepository,
)


@pytest.fixture
def important_dates():
    factory = create_session_factory(create_database_engine(Settings()))
    suffix = uuid4().hex[:12]
    user_ids = (str(uuid4()), str(uuid4()))
    with factory.begin() as session:
        for index, user_id in enumerate(user_ids):
            session.add(
                AppUser(
                    user_id=user_id,
                    username=f"calendar-{index}-{suffix}",
                    display_name=f"日历测试用户{index}",
                    password_hash="test-only-hash",
                    status=AppUserStatus.ACTIVE,
                    password_changed_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
    try:
        yield SqlAlchemyImportantDateRepository(factory), user_ids
    finally:
        with factory.begin() as session:
            owners = list(session.scalars(select(AppUser.id).where(AppUser.user_id.in_(user_ids))))
            session.execute(delete(ImportantDate).where(ImportantDate.user_id.in_(owners)))
            session.execute(delete(AppUser).where(AppUser.id.in_(owners)))


def test_create_is_transactional_unique_and_owner_scoped(important_dates) -> None:
    repository, (owner_a, owner_b) = important_dates
    created = repository.create(owner_a, date(2026, 8, 8), "  财报   发布 ", None)
    assert created.title == "财报 发布"
    assert [
        row.important_date_id
        for row in repository.list_range(owner_a, date(2026, 8, 1), date(2026, 8, 31))
    ] == [created.important_date_id]
    assert repository.list_range(owner_b, date(2026, 8, 1), date(2026, 8, 31)) == []
    with pytest.raises(ImportantDateConflict):
        repository.create(owner_a, date(2026, 8, 8), "财报 发布", None)


def test_update_and_delete_reject_cross_owner_access(important_dates) -> None:
    repository, (owner_a, owner_b) = important_dates
    created = repository.create(owner_a, date(2026, 8, 8), "事件", None)
    with pytest.raises(ImportantDateNotFound):
        repository.update(owner_b, created.important_date_id, date(2026, 8, 9), "事件", None)
    with pytest.raises(ImportantDateNotFound):
        repository.delete(owner_b, created.important_date_id)
    repository.delete(owner_a, created.important_date_id)
    assert repository.list_range(owner_a, date(2026, 8, 1), date(2026, 8, 31)) == []
