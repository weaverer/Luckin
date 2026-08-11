from types import SimpleNamespace

import pytest

from lucking.services.watchlist import CapacityExceeded, WatchlistService


class Repository:
    def __init__(self):
        self.groups = []
        self.members = []
        self.deleted = []

    def create_group(self, user_id, name, name_key, notes, tags):
        self.groups.append((user_id, name, name_key, notes, tags))
        return SimpleNamespace(group_id="group-1")

    def get_group_view(self, user_id, group_id):
        return SimpleNamespace(group_id=group_id, members=[])

    def count_members(self, user_id, group_id):
        return len(self.members)

    def add_member(self, user_id, group_id, stock_id):
        self.members.append((user_id, group_id, stock_id))
        return self.members[-1]

    def reorder_groups(self, user_id, group_ids):
        self.reordered = (user_id, group_ids)

    def delete_group(self, user_id, group_id):
        self.deleted.append(("group", user_id, group_id))

    def remove_member(self, user_id, group_id, member_id):
        self.deleted.append(("member", user_id, group_id, member_id))


def test_group_fields_are_normalized_and_owner_is_forwarded() -> None:
    repository = Repository()
    WatchlistService(repository).create_group(
        "owner-a", "  长线   持有 ", "  核心   资产 ", ["价值", "价值", " 长期 "]
    )
    assert repository.groups == [
        ("owner-a", "长线 持有", "长线 持有", "核心 资产", ["价值", "长期"])
    ]


def test_group_count_is_not_limited() -> None:
    repository = Repository()
    repository.groups = [object()] * 20
    WatchlistService(repository).create_group("owner-a", "新分组", "备注", ["标签"])
    assert len(repository.groups) == 21


def test_group_required_metadata_and_order_are_validated() -> None:
    repository = Repository()
    service = WatchlistService(repository)
    with pytest.raises(ValueError, match="备注"):
        service.create_group("owner-a", "新分组", " ", ["标签"])
    with pytest.raises(ValueError, match="标签"):
        service.create_group("owner-a", "新分组", "备注", [])
    with pytest.raises(ValueError, match="重复"):
        service.reorder_groups("owner-a", ["group-1", "group-1"])


def test_member_capacity_and_explicit_deletes_preserve_owner_scope() -> None:
    repository = Repository()
    service = WatchlistService(repository)
    repository.members = [object()] * 200
    with pytest.raises(CapacityExceeded):
        service.add_member("owner-a", "group-1", "stock-1")
    repository.members = []
    service.add_member("owner-a", "group-1", "stock-1")
    service.remove_member("owner-a", "group-1", "member-1")
    service.delete_group("owner-a", "group-1")
    assert repository.members == [("owner-a", "group-1", "stock-1")]
    assert repository.deleted == [
        ("member", "owner-a", "group-1", "member-1"),
        ("group", "owner-a", "group-1"),
    ]
