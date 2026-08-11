"""Transactional, user-scoped watchlist persistence."""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.stock_list import StockCurrent
from lucking.models.workbench import AppUser, WatchlistGroup, WatchlistMember
from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.repositories.stock_list import StockListItem


class WatchlistNotFound(LookupError):
    pass


class WatchlistNameConflict(ValueError):
    pass


class WatchlistMemberConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WatchlistMemberView:
    member_id: str
    stock: StockListItem
    sort_order: int


@dataclass(frozen=True, slots=True)
class WatchlistGroupView:
    group_id: str
    name: str
    notes: str
    tags: list[str]
    sort_order: int
    members: list[WatchlistMemberView]


class SqlAlchemyWatchlistRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def _owner(self, session: Session, user_id: str) -> int:
        owner = session.scalar(select(AppUser.id).where(AppUser.user_id == user_id))
        if owner is None:
            raise WatchlistNotFound("用户不存在")
        return owner

    def _group(self, session: Session, user_id: str, group_id: str) -> WatchlistGroup:
        group = session.scalar(
            select(WatchlistGroup).where(
                WatchlistGroup.group_id == group_id,
                WatchlistGroup.user_id == self._owner(session, user_id),
            )
        )
        if group is None:
            raise WatchlistNotFound("分组不存在")
        return group

    def create_group(
        self, user_id: str, name: str, name_key: str, notes: str, tags: list[str]
    ) -> WatchlistGroup:
        try:
            with self._sessions.begin() as session:
                owner = self._owner(session, user_id)
                sort_order = (
                    int(
                        session.scalar(
                            select(func.coalesce(func.max(WatchlistGroup.sort_order), -1)).where(
                                WatchlistGroup.user_id == owner
                            )
                        )
                        or 0
                    )
                    + 1
                )
                group = WatchlistGroup(
                    group_id=str(uuid4()),
                    user_id=owner,
                    name=name,
                    name_key=name_key,
                    notes=notes,
                    tags=tags,
                    sort_order=sort_order,
                )
                session.add(group)
                session.flush()
                session.refresh(group)
                session.expunge(group)
                return group
        except IntegrityError as exc:
            raise WatchlistNameConflict("分组名称已存在") from exc

    def list_groups(self, user_id: str) -> list[WatchlistGroup]:
        with self._sessions() as session:
            rows = list(
                session.scalars(
                    select(WatchlistGroup)
                    .where(WatchlistGroup.user_id == self._owner(session, user_id))
                    .order_by(WatchlistGroup.sort_order, WatchlistGroup.group_id)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def list_group_views(self, user_id: str) -> list[WatchlistGroupView]:
        with self._sessions() as session:
            groups = list(
                session.scalars(
                    select(WatchlistGroup)
                    .where(WatchlistGroup.user_id == self._owner(session, user_id))
                    .order_by(WatchlistGroup.sort_order, WatchlistGroup.group_id)
                )
            )
            result: list[WatchlistGroupView] = []
            for group in groups:
                rows = session.execute(
                    select(WatchlistMember, StockCurrent)
                    .join(StockCurrent, StockCurrent.stock_id == WatchlistMember.stock_id)
                    .where(WatchlistMember.group_id == group.id)
                    .order_by(WatchlistMember.sort_order, WatchlistMember.member_id)
                ).all()
                result.append(
                    WatchlistGroupView(
                        group_id=group.group_id,
                        name=group.name,
                        notes=group.notes,
                        tags=list(group.tags),
                        sort_order=group.sort_order,
                        members=[
                            WatchlistMemberView(
                                member_id=member.member_id,
                                stock=StockListItem(
                                    stock_id=stock.stock_id,
                                    market_code=stock.market_code,
                                    venue_code=VenueCode(stock.venue_code),
                                    security_code=stock.security_code,
                                    display_name=stock.display_name,
                                    currency_code=stock.currency_code,
                                    listing_status=ListingStatus(stock.listing_status),
                                    listed_on=stock.listed_on,
                                    delisted_on=stock.delisted_on,
                                ),
                                sort_order=member.sort_order,
                            )
                            for member, stock in rows
                        ],
                    )
                )
            return result

    def get_group_view(self, user_id: str, group_id: str) -> WatchlistGroupView:
        groups = self.list_group_views(user_id)
        group = next((item for item in groups if item.group_id == group_id), None)
        if group is None:
            raise WatchlistNotFound("分组不存在")
        return group

    def update_group(
        self,
        user_id: str,
        group_id: str,
        name: str,
        name_key: str,
        notes: str,
        tags: list[str],
    ) -> WatchlistGroup:
        try:
            with self._sessions.begin() as session:
                group = self._group(session, user_id, group_id)
                group.name = name
                group.name_key = name_key
                group.notes = notes
                group.tags = tags
                session.flush()
                session.refresh(group)
                session.expunge(group)
                return group
        except IntegrityError as exc:
            raise WatchlistNameConflict("分组名称已存在") from exc

    def reorder_groups(self, user_id: str, group_ids: list[str]) -> None:
        with self._sessions.begin() as session:
            owner = self._owner(session, user_id)
            groups = list(
                session.scalars(select(WatchlistGroup).where(WatchlistGroup.user_id == owner))
            )
            by_id = {group.group_id: group for group in groups}
            if len(group_ids) != len(groups) or set(group_ids) != set(by_id):
                raise WatchlistNotFound("分组排序数据不完整")
            for sort_order, group_id in enumerate(group_ids):
                by_id[group_id].sort_order = sort_order

    def delete_group(self, user_id: str, group_id: str) -> None:
        with self._sessions.begin() as session:
            group = self._group(session, user_id, group_id)
            session.query(WatchlistMember).filter(WatchlistMember.group_id == group.id).delete()
            session.delete(group)

    def add_member(self, user_id: str, group_id: str, stock_id: str) -> WatchlistMember:
        try:
            with self._sessions.begin() as session:
                group = self._group(session, user_id, group_id)
                if session.get(StockCurrent, stock_id) is None:
                    raise WatchlistNotFound("股票不存在")
                order = int(
                    session.scalar(
                        select(func.count())
                        .select_from(WatchlistMember)
                        .where(WatchlistMember.group_id == group.id)
                    )
                    or 0
                )
                member = WatchlistMember(
                    member_id=str(uuid4()), group_id=group.id, stock_id=stock_id, sort_order=order
                )
                session.add(member)
                session.flush()
                session.refresh(member)
                session.expunge(member)
                return member
        except IntegrityError as exc:
            raise WatchlistMemberConflict("股票已在分组中") from exc

    def list_members(self, user_id: str, group_id: str) -> list[WatchlistMember]:
        with self._sessions() as session:
            group = self._group(session, user_id, group_id)
            rows = list(
                session.scalars(
                    select(WatchlistMember)
                    .where(WatchlistMember.group_id == group.id)
                    .order_by(WatchlistMember.sort_order, WatchlistMember.member_id)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def count_members(self, user_id: str, group_id: str) -> int:
        with self._sessions() as session:
            group = self._group(session, user_id, group_id)
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(WatchlistMember)
                    .where(WatchlistMember.group_id == group.id)
                )
                or 0
            )

    def remove_member(self, user_id: str, group_id: str, stock_id: str) -> None:
        with self._sessions.begin() as session:
            group = self._group(session, user_id, group_id)
            member = session.scalar(
                select(WatchlistMember).where(
                    WatchlistMember.stock_id == stock_id,
                    WatchlistMember.group_id == group.id,
                )
            )
            if member is None:
                raise WatchlistNotFound("成员不存在")
            session.delete(member)
