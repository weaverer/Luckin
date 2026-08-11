from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.models.stock_list import StockCurrent
from lucking.models.workbench import AppUser, AppUserStatus, WatchlistGroup, WatchlistMember
from lucking.repositories.workbench.watchlists import (
    SqlAlchemyWatchlistRepository,
    WatchlistNotFound,
)


@pytest.fixture
def watchlists():
    factory = create_session_factory(create_database_engine(Settings()))
    stamp = datetime.now(UTC).replace(tzinfo=None)
    users = (str(uuid4()), str(uuid4()))
    stock_id = str(uuid4())
    with factory.begin() as session:
        for index, user_id in enumerate(users):
            session.add(
                AppUser(
                    user_id=user_id,
                    username=f"watch-{index}-{uuid4().hex[:8]}",
                    display_name=f"自选测试{index}",
                    password_hash="test",
                    status=AppUserStatus.ACTIVE,
                    password_changed_at=stamp,
                )
            )
        session.add(
            StockCurrent(
                stock_id=stock_id,
                market_code="CN-S",
                venue_code="XSHG",
                security_code=f"9{uuid4().hex[:5]}",
                display_name="测试股票",
                currency_code="CNY",
                listing_status="ACTIVE",
                listed_on=None,
                delisted_on=None,
                last_seen_run_id=str(uuid4()),
                last_seen_at=stamp,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    try:
        yield SqlAlchemyWatchlistRepository(factory), users, stock_id
    finally:
        with factory.begin() as session:
            owners = list(session.scalars(select(AppUser.id).where(AppUser.user_id.in_(users))))
            group_ids = list(
                session.scalars(select(WatchlistGroup.id).where(WatchlistGroup.user_id.in_(owners)))
            )
            session.execute(delete(WatchlistMember).where(WatchlistMember.group_id.in_(group_ids)))
            session.execute(delete(WatchlistGroup).where(WatchlistGroup.id.in_(group_ids)))
            session.execute(delete(AppUser).where(AppUser.id.in_(owners)))
            session.execute(delete(StockCurrent).where(StockCurrent.stock_id == stock_id))


def test_group_and_member_persist_with_stable_owner_order(watchlists) -> None:
    repository, (owner, _other), stock_id = watchlists
    group = repository.create_group(owner, "长线", "长线", "核心持仓", ["价值"])
    member = repository.add_member(owner, group.group_id, stock_id)
    view = repository.list_group_views(owner)[0]
    assert view.group_id == group.group_id
    assert view.notes == "核心持仓"
    assert view.tags == ["价值"]
    assert repository.list_members(owner, group.group_id)[0].member_id == member.member_id


def test_cross_owner_access_is_not_found(watchlists) -> None:
    repository, (owner, other), _stock_id = watchlists
    group = repository.create_group(owner, "隔离", "隔离", "权限测试", ["测试"])
    with pytest.raises(WatchlistNotFound):
        repository.list_members(other, group.group_id)


def test_group_order_is_persisted(watchlists) -> None:
    repository, (owner, _other), _stock_id = watchlists
    first = repository.create_group(owner, "第一组", "第一组", "备注一", ["标签"])
    second = repository.create_group(owner, "第二组", "第二组", "备注二", ["标签"])

    repository.reorder_groups(owner, [second.group_id, first.group_id])

    assert [group.group_id for group in repository.list_group_views(owner)] == [
        second.group_id,
        first.group_id,
    ]
