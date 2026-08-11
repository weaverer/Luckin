"""Strongly typed broker recommendation read API."""

from datetime import date, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from lucking.api.dependencies import get_current_session, get_request_id, get_settings
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, PageData, Pagination, success_response
from lucking.db import create_database_engine, create_session_factory
from lucking.repositories.stock_list import StockListItem
from lucking.repositories.workbench_queries.broker_recommendations import (
    BrokerRecommendationItem,
    BrokerRecommendationQueryRepository,
)
from lucking.services.auth import AuthenticatedSession
from lucking.services.broker_recommendation_query import BrokerRecommendationQuery

router = APIRouter(prefix="/broker-recommendations", tags=["broker-recommendations"])


class StockDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_id: str
    market_code: str
    venue_code: str
    security_code: str
    name: str
    listing_status: str


class RecommendationDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    recommendation_month: date
    broker_name: str
    stock: StockDto
    updated_at: datetime


@lru_cache
def query_service() -> BrokerRecommendationQuery:
    sessions = create_session_factory(create_database_engine(get_settings()))
    return BrokerRecommendationQuery(BrokerRecommendationQueryRepository(sessions))


def stock_dto(stock: StockListItem) -> StockDto:
    return StockDto(
        stock_id=stock.stock_id,
        market_code=stock.market_code,
        venue_code=stock.venue_code.value,
        security_code=stock.security_code,
        name=stock.display_name,
        listing_status=stock.listing_status.value,
    )


def recommendation_dto(item: BrokerRecommendationItem) -> RecommendationDto:
    return RecommendationDto(
        recommendation_id=item.recommendation_id,
        recommendation_month=item.recommendation_month,
        broker_name=item.broker_name,
        stock=stock_dto(item.stock),
        updated_at=item.updated_at,
    )


@router.get(
    "",
    response_model=ApiResponse[PageData[RecommendationDto]],
    operation_id="listBrokerRecommendations",
)
async def list_recommendations(
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
    recommendation_month: date,
    broker_name: Annotated[str | None, Query(max_length=160)] = None,
    stock_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApiResponse[PageData[RecommendationDto]]:
    try:
        page = query_service().list(recommendation_month, broker_name, stock_id, limit, offset)
    except ValueError as exc:
        raise ApiError(400, BusinessErrorCode.REQUEST_VALIDATION_FAILED, str(exc)) from exc
    items = [recommendation_dto(item) for item in page.items]
    return success_response(
        PageData(
            items=items,
            pagination=Pagination(
                limit=page.limit,
                offset=page.offset,
                total=page.total,
                has_more=page.offset + len(items) < page.total,
            ),
        ),
        request_id,
    )
