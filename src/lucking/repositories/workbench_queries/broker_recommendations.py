"""Read-only broker recommendation queries composed with canonical stocks."""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lucking.models.broker_recommendation import BrokerRecommendation
from lucking.models.stock_list import StockCurrent
from lucking.ports.stock_list_provider import ListingStatus, VenueCode
from lucking.repositories.stock_list import StockListItem


@dataclass(frozen=True, slots=True)
class BrokerRecommendationItem:
    recommendation_id: str
    recommendation_month: date
    broker_name: str
    stock: StockListItem
    updated_at: datetime


class BrokerRecommendationQueryRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list(
        self,
        *,
        recommendation_month: date,
        broker_name: str | None,
        stock_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[BrokerRecommendationItem], int]:
        filters = [BrokerRecommendation.recommendation_month == recommendation_month]
        if broker_name:
            filters.append(BrokerRecommendation.broker_name.contains(broker_name))
        if stock_id:
            filters.append(BrokerRecommendation.stock_id == stock_id)
        statement = (
            select(BrokerRecommendation, StockCurrent)
            .join(StockCurrent, StockCurrent.stock_id == BrokerRecommendation.stock_id)
            .where(*filters)
            .order_by(
                BrokerRecommendation.broker_name,
                StockCurrent.venue_code,
                StockCurrent.security_code,
                BrokerRecommendation.recommendation_id,
            )
            .limit(limit)
            .offset(offset)
        )
        with self._sessions() as session:
            total = int(
                session.scalar(
                    select(func.count()).select_from(BrokerRecommendation).where(*filters)
                )
                or 0
            )
            rows = session.execute(statement).all()
        return [self._item(recommendation, stock) for recommendation, stock in rows], total

    @staticmethod
    def _item(
        recommendation: BrokerRecommendation, stock: StockCurrent
    ) -> BrokerRecommendationItem:
        return BrokerRecommendationItem(
            recommendation_id=recommendation.recommendation_id,
            recommendation_month=recommendation.recommendation_month,
            broker_name=recommendation.broker_name,
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
            updated_at=recommendation.updated_at,
        )
