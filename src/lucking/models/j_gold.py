"""供应商无关的 J金股研究查询模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

RadarFilter = Literal["monthly", "new", "consensus", "warming", "breakout", "excess"]


class QualityKind(StrEnum):
    READY = "ready"
    EMPTY = "empty"
    DELAYED = "delayed"
    INSUFFICIENT = "insufficient"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class QualityStatus:
    status: QualityKind
    explanation: str
    source: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class RecommendationFact:
    recommendation_month: date
    broker_name: str
    stock_id: str
    security_code: str
    stock_name: str
    listing_status: str
    industry: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class QuotePoint:
    trade_date: date
    close: float
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchContext:
    recommendation_month: date
    broker_name: str | None = None
    industry: str | None = None
    limit: int = 50
    offset: int = 0
    sort_by: str = "score"
    sort_direction: str = "desc"
    radar_filter: RadarFilter | None = None


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    metrics: dict[str, Any]
    items: list[dict[str, Any]]
    pagination: dict[str, Any]
    signals: list[dict[str, Any]]
    industries: list[dict[str, Any]]
    broker_ability: list[dict[str, Any]]
    diffusion: list[dict[str, Any]]
    quality: QualityStatus
    selected_month: date
    available_months: list[date]
