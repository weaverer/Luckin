"""Validation and paging rules for broker recommendation queries."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from lucking.repositories.workbench_queries.broker_recommendations import (
    BrokerRecommendationItem,
)


class Recommendations(Protocol):
    def list(
        self,
        *,
        recommendation_month: date,
        broker_name: str | None,
        stock_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BrokerRecommendationItem], int]: ...


@dataclass(frozen=True, slots=True)
class BrokerRecommendationPage:
    items: list[BrokerRecommendationItem]
    total: int
    limit: int
    offset: int


class BrokerRecommendationQuery:
    def __init__(self, repository: Recommendations) -> None:
        self._repository = repository

    def list(
        self,
        recommendation_month: date,
        broker_name: str | None,
        stock_id: str | None,
        limit: int,
        offset: int,
    ) -> BrokerRecommendationPage:
        if recommendation_month.day != 1:
            raise ValueError("推荐月份必须为月首")
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("分页参数非法")
        normalized_broker = " ".join(broker_name.split()) if broker_name else None
        items, total = self._repository.list(
            recommendation_month=recommendation_month,
            broker_name=normalized_broker,
            stock_id=stock_id,
            limit=limit,
            offset=offset,
        )
        return BrokerRecommendationPage(items, total, limit, offset)
