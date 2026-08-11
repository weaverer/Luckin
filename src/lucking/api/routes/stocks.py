"""Strongly typed stock and daily quote endpoints."""

from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from lucking.api.dependencies import get_current_session, get_request_id, get_settings
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, PageData, Pagination, success_response
from lucking.clickhouse import build_clickhouse_client
from lucking.db import create_database_engine, create_session_factory
from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.stock_list import StockListItem
from lucking.repositories.workbench_queries.stocks import (
    DailyQuoteQueryRepository,
    StockQueryRepository,
)
from lucking.services.auth import AuthenticatedSession
from lucking.services.stock_workspace import QuoteStatus, StockDetail, StockWorkspace

router = APIRouter(prefix="/stocks", tags=["stocks"])


class StockDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_id: str
    market_code: Literal["CN-S"]
    venue_code: VenueCode
    security_code: str
    name: str
    listing_status: ListingStatus


class QuoteDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: date
    open: str
    high: str
    low: str
    close: str
    pre_close: str
    change: str
    pct_chg: str
    vol: str
    amount: str
    updated_at: datetime


class StockDetailDto(StockDto):
    latest_quote: QuoteDto | None
    market_data_status: QuoteStatus


@lru_cache
def workspace() -> StockWorkspace:
    settings = get_settings()
    sessions = create_session_factory(create_database_engine(settings))
    quotes = MarketDataClickHouseRepository(build_clickhouse_client(settings))
    return StockWorkspace(StockQueryRepository(sessions), DailyQuoteQueryRepository(quotes))


def stock_dto(row: StockListItem) -> StockDto:
    return StockDto(
        stock_id=row.stock_id,
        market_code=cast(Literal["CN-S"], row.market_code),
        venue_code=row.venue_code,
        security_code=row.security_code,
        name=row.display_name,
        listing_status=row.listing_status,
    )


def quote_dto(row: dict[str, object]) -> QuoteDto:
    return QuoteDto.model_validate(
        {
            field: row[field]
            for field in (
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "change",
                "pct_chg",
                "vol",
                "amount",
                "updated_at",
            )
        }
    )


def detail_dto(detail: StockDetail) -> StockDetailDto:
    stock = stock_dto(detail.stock)
    return StockDetailDto(
        **stock.model_dump(),
        latest_quote=quote_dto(detail.latest_quote) if detail.latest_quote else None,
        market_data_status=detail.market_data_status,
    )


@router.get("", response_model=ApiResponse[PageData[StockDto]], operation_id="listStocks")
async def list_stocks(
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
    query: Annotated[str | None, Query(max_length=80)] = None,
    venue_code: Annotated[str | None, Query(pattern="^(XSHG|XSHE|XBSE)$")] = None,
    listing_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiResponse[PageData[StockDto]]:
    page = workspace().search(query or "", limit, offset, venue_code, listing_status)
    return success_response(
        PageData(
            items=[stock_dto(row) for row in page.items],
            pagination=Pagination(
                limit=page.limit,
                offset=page.offset,
                total=page.total,
                has_more=page.offset + len(page.items) < page.total,
            ),
        ),
        request_id,
    )


@router.get("/{stock_id}", response_model=ApiResponse[StockDetailDto], operation_id="getStock")
async def get_stock(
    stock_id: str,
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> ApiResponse[StockDetailDto]:
    detail = workspace().get(stock_id)
    if detail is None:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, "股票不存在")
    return success_response(detail_dto(detail), request_id)


@router.get(
    "/{stock_id}/daily-quotes",
    response_model=ApiResponse[list[QuoteDto]],
    operation_id="listDailyQuotes",
)
async def list_daily_quotes(
    stock_id: str,
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=400)] = 120,
) -> ApiResponse[list[QuoteDto]]:
    if workspace().get(stock_id) is None:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, "股票不存在")
    try:
        result = workspace().quotes(stock_id, limit, start_date, end_date)
    except ValueError as exc:
        raise ApiError(400, BusinessErrorCode.QUERY_RANGE_INVALID, str(exc)) from exc
    return success_response([quote_dto(row) for row in result.items], request_id)
