"""User-owned watchlist rules."""

from typing import Any, Protocol

from lucking.repositories.workbench.watchlists import WatchlistGroupView


class CapacityExceeded(ValueError):
    pass


class Watchlists(Protocol):
    def create_group(
        self, user_id: str, name: str, name_key: str, notes: str, tags: list[str]
    ) -> Any: ...
    def list_group_views(self, user_id: str) -> list[WatchlistGroupView]: ...
    def get_group_view(self, user_id: str, group_id: str) -> WatchlistGroupView: ...
    def count_members(self, user_id: str, group_id: str) -> int: ...
    def add_member(self, user_id: str, group_id: str, stock_id: str) -> Any: ...
    def update_group(
        self,
        user_id: str,
        group_id: str,
        name: str,
        name_key: str,
        notes: str,
        tags: list[str],
    ) -> Any: ...
    def reorder_groups(self, user_id: str, group_ids: list[str]) -> None: ...
    def delete_group(self, user_id: str, group_id: str) -> None: ...
    def remove_member(self, user_id: str, group_id: str, stock_id: str) -> None: ...


class WatchlistService:
    def __init__(self, repository: Watchlists) -> None:
        self._repository = repository

    def list_groups(self, user_id: str) -> list[WatchlistGroupView]:
        return self._repository.list_group_views(user_id)

    def get_group(self, user_id: str, group_id: str) -> WatchlistGroupView:
        return self._repository.get_group_view(user_id, group_id)

    def create_group(
        self, user_id: str, name: str, notes: str, tags: list[str]
    ) -> WatchlistGroupView:
        display = self._normalize_name(name)
        clean_notes = self._normalize_notes(notes)
        clean_tags = self._normalize_tags(tags)
        group = self._repository.create_group(
            user_id, display, display.casefold(), clean_notes, clean_tags
        )
        return self._repository.get_group_view(user_id, group.group_id)

    def add_member(self, user_id: str, group_id: str, stock_id: str) -> WatchlistGroupView:
        if self._repository.count_members(user_id, group_id) >= 200:
            raise CapacityExceeded("每组最多 200 只股票")
        self._repository.add_member(user_id, group_id, stock_id)
        return self._repository.get_group_view(user_id, group_id)

    def update_group(
        self, user_id: str, group_id: str, name: str, notes: str, tags: list[str]
    ) -> WatchlistGroupView:
        display = self._normalize_name(name)
        self._repository.update_group(
            user_id,
            group_id,
            display,
            display.casefold(),
            self._normalize_notes(notes),
            self._normalize_tags(tags),
        )
        return self._repository.get_group_view(user_id, group_id)

    def reorder_groups(self, user_id: str, group_ids: list[str]) -> list[WatchlistGroupView]:
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("分组排序不能包含重复项")
        self._repository.reorder_groups(user_id, group_ids)
        return self._repository.list_group_views(user_id)

    def delete_group(self, user_id: str, group_id: str) -> None:
        self._repository.delete_group(user_id, group_id)

    def remove_member(self, user_id: str, group_id: str, stock_id: str) -> None:
        self._repository.remove_member(user_id, group_id, stock_id)

    @staticmethod
    def _normalize_name(name: str) -> str:
        display = " ".join(name.split())
        if not display:
            raise ValueError("分组名称不能为空")
        return display

    @staticmethod
    def _normalize_notes(notes: str) -> str:
        display = " ".join(notes.split())
        if not display:
            raise ValueError("分组备注不能为空")
        return display

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in tags:
            tag = " ".join(value.split())
            if len(tag) > 30:
                raise ValueError("分组标签不能超过 30 个字符")
            if tag and tag not in normalized:
                normalized.append(tag)
        if not normalized:
            raise ValueError("请至少添加一个分组标签")
        return normalized
