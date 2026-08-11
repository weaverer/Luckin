"""Strongly typed, user-owned watchlist endpoints."""

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from lucking.api.dependencies import get_current_session, get_request_id, get_settings, require_csrf
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, success_response
from lucking.db import create_database_engine, create_session_factory
from lucking.repositories.stock_list import StockListItem
from lucking.repositories.workbench.watchlists import (
    SqlAlchemyWatchlistRepository,
    WatchlistGroupView,
    WatchlistMemberConflict,
    WatchlistMemberView,
    WatchlistNameConflict,
    WatchlistNotFound,
)
from lucking.services.auth import AuthenticatedSession
from lucking.services.watchlist import CapacityExceeded, WatchlistService

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


class GroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    notes: str = Field(min_length=1, max_length=1000)
    tags: list[str] = Field(min_length=1, max_length=20)


class GroupOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_ids: list[str]


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_id: str


class StockDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_id: str
    market_code: str
    venue_code: str
    security_code: str
    name: str
    listing_status: str


class MemberDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str
    stock: StockDto
    sort_order: int = Field(ge=0)


class GroupDto(GroupInput):
    group_id: str
    sort_order: int = Field(ge=0)
    members: list[MemberDto]


@lru_cache
def service() -> WatchlistService:
    sessions = create_session_factory(create_database_engine(get_settings()))
    return WatchlistService(SqlAlchemyWatchlistRepository(sessions))


def stock_dto(stock: StockListItem) -> StockDto:
    return StockDto(
        stock_id=stock.stock_id,
        market_code=stock.market_code,
        venue_code=stock.venue_code.value,
        security_code=stock.security_code,
        name=stock.display_name,
        listing_status=stock.listing_status.value,
    )


def member_dto(member: WatchlistMemberView) -> MemberDto:
    return MemberDto(
        member_id=member.member_id,
        stock=stock_dto(member.stock),
        sort_order=member.sort_order,
    )


def group_dto(group: WatchlistGroupView) -> GroupDto:
    return GroupDto(
        group_id=group.group_id,
        name=group.name,
        notes=group.notes,
        tags=group.tags,
        sort_order=group.sort_order,
        members=[member_dto(member) for member in group.members],
    )


def map_action[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except WatchlistNotFound as exc:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc
    except WatchlistNameConflict as exc:
        raise ApiError(409, BusinessErrorCode.WATCHLIST_NAME_CONFLICT, str(exc)) from exc
    except WatchlistMemberConflict as exc:
        raise ApiError(409, BusinessErrorCode.WATCHLIST_MEMBER_CONFLICT, str(exc)) from exc
    except CapacityExceeded as exc:
        raise ApiError(409, BusinessErrorCode.WATCHLIST_CAPACITY_EXCEEDED, str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, BusinessErrorCode.REQUEST_VALIDATION_FAILED, str(exc)) from exc


@router.get("", response_model=ApiResponse[list[GroupDto]], operation_id="listWatchlists")
async def list_groups(
    request_id: Annotated[str, Depends(get_request_id)],
    auth: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> ApiResponse[list[GroupDto]]:
    groups = map_action(lambda: service().list_groups(auth.user_id))
    return success_response([group_dto(group) for group in groups], request_id)


@router.post(
    "", status_code=201, response_model=ApiResponse[GroupDto], operation_id="createWatchlist"
)
async def create_group(
    body: GroupInput,
    request_id: Annotated[str, Depends(get_request_id)],
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[GroupDto]:
    group = map_action(
        lambda: service().create_group(auth.user_id, body.name, body.notes, body.tags)
    )
    return success_response(group_dto(group), request_id)


@router.put("/order", response_model=ApiResponse[list[GroupDto]], operation_id="orderWatchlists")
async def order_groups(
    body: GroupOrderInput,
    request_id: Annotated[str, Depends(get_request_id)],
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[list[GroupDto]]:
    groups = map_action(lambda: service().reorder_groups(auth.user_id, body.group_ids))
    return success_response([group_dto(group) for group in groups], request_id)


@router.put("/{group_id}", response_model=ApiResponse[GroupDto], operation_id="updateWatchlist")
async def update_group(
    group_id: str,
    body: GroupInput,
    request_id: Annotated[str, Depends(get_request_id)],
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[GroupDto]:
    group = map_action(
        lambda: service().update_group(auth.user_id, group_id, body.name, body.notes, body.tags)
    )
    return success_response(group_dto(group), request_id)


@router.delete("/{group_id}", status_code=204, operation_id="deleteWatchlist")
async def delete_group(
    group_id: str,
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> Response:
    map_action(lambda: service().delete_group(auth.user_id, group_id))
    return Response(status_code=204)


@router.post(
    "/{group_id}/members",
    status_code=201,
    response_model=ApiResponse[MemberDto],
    operation_id="addWatchlistMember",
)
async def add_member(
    group_id: str,
    body: MemberInput,
    request_id: Annotated[str, Depends(get_request_id)],
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[MemberDto]:
    group = map_action(lambda: service().add_member(auth.user_id, group_id, body.stock_id))
    member = next(item for item in group.members if item.stock.stock_id == body.stock_id)
    return success_response(member_dto(member), request_id)


@router.delete(
    "/{group_id}/members/{stock_id}",
    status_code=204,
    operation_id="removeWatchlistMember",
)
async def remove_member(
    group_id: str,
    stock_id: str,
    auth: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> Response:
    map_action(lambda: service().remove_member(auth.user_id, group_id, stock_id))
    return Response(status_code=204)
