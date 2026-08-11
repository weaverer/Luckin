"""Trading calendar and user-owned important dates."""

from collections.abc import Callable
from datetime import date as Date
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from lucking.api.dependencies import get_current_session, get_request_id, get_settings, require_csrf
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, success_response
from lucking.db import create_database_engine, create_session_factory
from lucking.models.workbench import ImportantDate
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository
from lucking.repositories.workbench.important_dates import (
    ImportantDateConflict,
    ImportantDateNotFound,
    SqlAlchemyImportantDateRepository,
)
from lucking.services.auth import AuthenticatedSession
from lucking.services.calendar_workspace import CalendarWorkspace, InvalidCalendarRange

router = APIRouter(tags=["calendar"])


class ImportantDateInput(BaseModel):
    event_date: Date
    title: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class ImportantDateDto(ImportantDateInput):
    important_date_id: str
    created_at: datetime
    updated_at: datetime


class CalendarDayDto(BaseModel):
    date: Date
    market_code: Literal["CN-S"]
    market_status: Literal["OPEN", "CLOSED", "UNKNOWN"]
    previous_open_date: Date | None
    calendar_updated_at: datetime | None
    important_dates: list[ImportantDateDto]


@lru_cache
def dependencies() -> tuple[CalendarWorkspace, SqlAlchemyImportantDateRepository]:
    factory = create_session_factory(create_database_engine(get_settings()))
    dates = SqlAlchemyImportantDateRepository(factory)
    return CalendarWorkspace(SqlAlchemyTradingCalendarRepository(factory), dates), dates


def dto(row: ImportantDate) -> ImportantDateDto:
    return ImportantDateDto.model_validate(
        {
            "important_date_id": row.important_date_id,
            "event_date": row.event_date,
            "title": row.title,
            "notes": row.notes,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


@router.get(
    "/calendar", response_model=ApiResponse[list[CalendarDayDto]], operation_id="listCalendar"
)
async def list_calendar(
    start_date: Date,
    end_date: Date,
    request_id: Annotated[str, Depends(get_request_id)],
    session: Annotated[AuthenticatedSession, Depends(get_current_session)],
    market_code: Annotated[str, Query(pattern="^CN-S$")] = "CN-S",
) -> ApiResponse[list[CalendarDayDto]]:
    try:
        days = dependencies()[0].list_calendar(session.user_id, start_date, end_date, market_code)
    except InvalidCalendarRange as exc:
        raise ApiError(400, BusinessErrorCode.QUERY_RANGE_INVALID, str(exc)) from exc
    return success_response(
        [
            CalendarDayDto(
                date=x.date,
                market_code=cast(Literal["CN-S"], x.market_code),
                market_status=cast(
                    Literal["OPEN", "CLOSED", "UNKNOWN"], x.market_status
                ),
                previous_open_date=x.previous_open_date,
                calendar_updated_at=x.calendar_updated_at,
                important_dates=[dto(v) for v in x.important_dates],
            )
            for x in days
        ],
        request_id,
    )


def map_write[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except ImportantDateConflict as exc:
        raise ApiError(409, BusinessErrorCode.IMPORTANT_DATE_CONFLICT, str(exc)) from exc
    except ImportantDateNotFound as exc:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, str(exc)) from exc


@router.post(
    "/important-dates",
    status_code=201,
    response_model=ApiResponse[ImportantDateDto],
    operation_id="createImportantDate",
)
async def create_date(
    body: ImportantDateInput,
    request_id: Annotated[str, Depends(get_request_id)],
    session: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[ImportantDateDto]:
    return success_response(
        dto(
            map_write(
                lambda: dependencies()[1].create(
                    session.user_id, body.event_date, body.title, body.notes
                )
            )
        ),
        request_id,
    )


@router.put(
    "/important-dates/{important_date_id}",
    response_model=ApiResponse[ImportantDateDto],
    operation_id="updateImportantDate",
)
async def update_date(
    important_date_id: str,
    body: ImportantDateInput,
    request_id: Annotated[str, Depends(get_request_id)],
    session: Annotated[AuthenticatedSession, Depends(require_csrf)],
) -> ApiResponse[ImportantDateDto]:
    return success_response(
        dto(
            map_write(
                lambda: dependencies()[1].update(
                    session.user_id, important_date_id, body.event_date, body.title, body.notes
                )
            )
        ),
        request_id,
    )


@router.delete(
    "/important-dates/{important_date_id}", status_code=204, operation_id="deleteImportantDate"
)
async def delete_date(
    important_date_id: str, session: Annotated[AuthenticatedSession, Depends(require_csrf)]
) -> Response:
    map_write(lambda: dependencies()[1].delete(session.user_id, important_date_id))
    return Response(status_code=204)
