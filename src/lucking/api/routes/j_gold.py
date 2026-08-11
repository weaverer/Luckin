"""J金股研究驾驶舱强类型读接口。"""

from datetime import date, datetime
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict

from lucking.api.dependencies import get_current_session, get_request_id, get_settings
from lucking.api.errors import ApiError, BusinessErrorCode
from lucking.api.responses import ApiResponse, Pagination, success_response
from lucking.clickhouse import build_clickhouse_client
from lucking.db import create_database_engine, create_session_factory
from lucking.models.j_gold import QualityKind, QualityStatus, ResearchContext
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.repositories.workbench_queries.j_gold import JGoldQueryRepository
from lucking.services.auth import AuthenticatedSession
from lucking.services.j_gold_research import JGoldResearchService

router = APIRouter(prefix="/j-gold", tags=["j-gold"])


class QualityDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: QualityKind
    explanation: str
    source: str
    generated_at: datetime


class MonthlyCountDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: date
    count: int | None


class MetricsDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_count: int
    broker_count: int
    industry_count: int
    new_count: int | None
    new_change: int | None
    consensus_count: int
    warming_count: int | None
    warming_three_months: list[MonthlyCountDto]
    breakout_count: int
    average_excess_20d: float | None
    excess_sample_count: int
    benchmark: str
    recommendation_month: date | None = None


class StockIdentityDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_id: str
    security_code: str
    name: str
    market_code: str
    listing_status: str


class RadarItemDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock: StockIdentityDto
    industry: str | None
    broker_count: int
    brokers: list[str]
    month_delta: int | None
    is_new: bool | None
    three_month_peak: bool | None
    breakout: bool
    consecutive_months: int
    excess_20d: float | None
    status: Literal["突破", "趋势强", "新晋", "高共识", "持续", "降温", "数据不足"]
    score: float | None
    score_components: dict[str, float | None]
    quality: QualityKind
    quality_explanation: str


class SignalDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: Literal["stock", "industry"]
    stock: StockIdentityDto | None
    industry: str | None
    type: str
    summary: str
    comparison_period: str
    trigger_rule: str
    data_time: date
    quality: QualityKind


class IndustryConsensusDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    industry: str
    recommendation_records: int
    stock_count: int
    broker_count: int
    month_delta: int | None
    heat_rank: int
    quality: QualityKind
    generated_at: datetime


class BrokerAbilityDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker_name: str
    sample_count: int
    average_excess_20d: float | None
    positive_ratio: float | None
    coverage: float
    minimum_sample_count: int
    grade: str | None
    period_start: date | None
    period_end: date | None
    benchmark: str
    return_basis: str
    quality: QualityKind
    generated_at: datetime


class DiffusionPointDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: date
    stock_count: int | None
    month_delta: int | None
    quality: QualityKind
    count_basis: str
    generated_at: datetime


class ResearchDataDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: MetricsDto
    items: list[RadarItemDto]
    pagination: Pagination
    signals: list[SignalDto]
    industries: list[IndustryConsensusDto]
    broker_ability: list[BrokerAbilityDto]
    diffusion: list[DiffusionPointDto]
    quality: QualityDto
    selected_month: date
    available_months: list[date]


class StockResearchDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock: StockIdentityDto
    industry: str | None
    recommendations: list["RecommendationDetailDto"]
    history: list["RecommendationHistoryDto"]
    latest_quote_date: date | None
    price_basis: str
    source: str
    generated_at: datetime
    quality: QualityKind


class RecommendationDetailDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker_name: str
    recommendation_month: date
    updated_at: datetime


class RecommendationHistoryDto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: date
    broker_count: int


StockResearchDto.model_rebuild()


@lru_cache
def service() -> JGoldResearchService:
    settings = get_settings()
    clickhouse = build_clickhouse_client(settings)
    sessions = create_session_factory(create_database_engine(settings))
    reader = JGoldQueryRepository(
        sessions,
        MarketDataClickHouseRepository(clickhouse),
        clickhouse,
    )
    return JGoldResearchService(reader)


def quality_dto(value: QualityStatus) -> QualityDto:
    return QualityDto(
        status=value.status,
        explanation=value.explanation,
        source=value.source,
        generated_at=value.generated_at,
    )


@router.get(
    "/research",
    response_model=ApiResponse[ResearchDataDto],
    operation_id="getJGoldResearch",
)
async def get_research(
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
    recommendation_month: date | None = None,
    broker_name: Annotated[str | None, Query(max_length=160)] = None,
    industry: Annotated[str | None, Query(max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: Literal[
        "score", "broker_count", "month_delta", "consecutive_months", "excess_20d"
    ] = "score",
    sort_direction: Literal["asc", "desc"] = "desc",
    radar_filter: Literal["monthly", "new", "consensus", "warming", "breakout", "excess"]
    | None = None,
) -> ApiResponse[ResearchDataDto]:
    requested = recommendation_month or date.today().replace(day=1)
    try:
        snapshot = service().research(
            ResearchContext(
                recommendation_month=requested,
                broker_name=" ".join(broker_name.split()) if broker_name else None,
                industry=industry,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_direction=sort_direction,
                radar_filter=radar_filter,
            )
        )
    except ValueError as exc:
        raise ApiError(400, BusinessErrorCode.REQUEST_VALIDATION_FAILED, str(exc)) from exc
    data = ResearchDataDto(
        metrics=MetricsDto.model_validate(snapshot.metrics),
        items=[RadarItemDto.model_validate(item) for item in snapshot.items],
        pagination=Pagination.model_validate(snapshot.pagination),
        signals=[SignalDto.model_validate(item) for item in snapshot.signals],
        industries=[IndustryConsensusDto.model_validate(item) for item in snapshot.industries],
        broker_ability=[BrokerAbilityDto.model_validate(item) for item in snapshot.broker_ability],
        diffusion=[DiffusionPointDto.model_validate(item) for item in snapshot.diffusion],
        quality=quality_dto(snapshot.quality),
        selected_month=snapshot.selected_month,
        available_months=snapshot.available_months,
    )
    return success_response(data, request_id)


@router.get(
    "/stocks/{stock_id}",
    response_model=ApiResponse[StockResearchDto],
    operation_id="getJGoldStockResearch",
)
async def get_stock_research(
    stock_id: Annotated[str, Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9-]+$")],
    recommendation_month: date,
    request_id: Annotated[str, Depends(get_request_id)],
    _: Annotated[AuthenticatedSession, Depends(get_current_session)],
) -> ApiResponse[StockResearchDto]:
    detail = service().stock_detail(stock_id, recommendation_month)
    if detail is None:
        raise ApiError(404, BusinessErrorCode.RESOURCE_NOT_FOUND, "该月份没有此股票的金股研究记录")
    return success_response(StockResearchDto.model_validate(detail), request_id)
