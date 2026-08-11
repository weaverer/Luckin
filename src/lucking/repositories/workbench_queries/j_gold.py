"""从现有 MySQL/ClickHouse 事实数据读取 J金股研究数据。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient
from lucking.models.broker_recommendation import (
    BrokerRecommendation,
    BrokerRecommendationSyncRun,
    BrokerRecommendationSyncStatus,
)
from lucking.models.j_gold import QuotePoint, RecommendationFact
from lucking.models.stock_list import StockCurrent
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository


class JGoldQueryRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        market_data: MarketDataClickHouseRepository,
        clickhouse: ClickHouseClient,
    ) -> None:
        self._sessions = sessions
        self._market_data = market_data
        self._clickhouse = clickhouse

    def available_months(self) -> list[date]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(BrokerRecommendationSyncRun.target_month)
                    .where(
                        BrokerRecommendationSyncRun.status
                        == BrokerRecommendationSyncStatus.SUCCEEDED.value,
                        BrokerRecommendationSyncRun.published_at.is_not(None),
                    )
                    .distinct()
                    .order_by(BrokerRecommendationSyncRun.target_month.desc())
                )
            )

    def recommendations(self, start_month: date, end_month: date) -> list[RecommendationFact]:
        statement = (
            select(BrokerRecommendation, StockCurrent)
            .join(StockCurrent, StockCurrent.stock_id == BrokerRecommendation.stock_id)
            .where(
                BrokerRecommendation.recommendation_month >= start_month,
                BrokerRecommendation.recommendation_month <= end_month,
            )
            .order_by(
                BrokerRecommendation.recommendation_month,
                BrokerRecommendation.stock_id,
                BrokerRecommendation.broker_name,
            )
        )
        with self._sessions() as session:
            rows = session.execute(statement).all()
        return [
            RecommendationFact(
                recommendation_month=rec.recommendation_month,
                broker_name=rec.broker_name,
                stock_id=rec.stock_id,
                security_code=stock.security_code,
                stock_name=stock.display_name,
                listing_status=stock.listing_status,
                industry=None,
                updated_at=rec.updated_at,
            )
            for rec, stock in rows
        ]

    def stock_quotes(
        self, stock_id: str, limit: int = 80, start_date: date | None = None
    ) -> list[QuotePoint]:
        rows = self._market_data.query_daily_quotes_post_adjusted(
            stock_id=stock_id,
            limit=limit,
            start_date=start_date,
            descending=start_date is None,
        )
        quotes = [self._quote(row) for row in rows]
        return quotes if start_date is not None else list(reversed(quotes))

    def stock_quotes_batch(
        self, stock_ids: list[str], limit: int = 400
    ) -> dict[str, list[QuotePoint]]:
        """单次读取多只股票的后复权行情，每只股票最多返回 ``limit`` 条。"""
        if not stock_ids:
            return {}
        if not 1 <= limit <= 400:
            raise ValueError("批量行情查询条数必须为 1 至 400")
        canonical_ids = sorted({str(UUID(stock_id)) for stock_id in stock_ids})
        id_list = ", ".join(f"'{stock_id}'" for stock_id in canonical_ids)
        rows = self._clickhouse.execute(
            f"""
            WITH first_factor AS (
                SELECT
                    stock_id,
                    argMinIf(adj_factor, trade_date, adj_factor > 0) AS factor_first
                FROM {self._clickhouse.database}.adj_factor FINAL
                WHERE stock_id IN ({id_list})
                GROUP BY stock_id
            )
            SELECT
                q.stock_id AS stock_id,
                q.trade_date AS trade_date,
                q.close * f.adj_factor / ff.factor_first AS close,
                q.updated_at AS updated_at
            FROM (
                SELECT stock_id, trade_date, close, updated_at
                FROM {self._clickhouse.database}.daily_quote FINAL
                WHERE stock_id IN ({id_list})
            ) AS q
            INNER JOIN (
                SELECT stock_id, trade_date, adj_factor
                FROM {self._clickhouse.database}.adj_factor FINAL
                WHERE stock_id IN ({id_list})
            ) AS f
                ON q.stock_id = f.stock_id AND q.trade_date = f.trade_date
            INNER JOIN first_factor AS ff ON q.stock_id = ff.stock_id
            WHERE f.adj_factor > 0
                AND ff.factor_first > 0
            ORDER BY q.stock_id, q.trade_date DESC
            LIMIT {limit} BY q.stock_id
            """
        )
        result: dict[str, list[QuotePoint]] = {stock_id: [] for stock_id in canonical_ids}
        for row in rows:
            result[str(row["stock_id"])].append(self._quote(row))
        for quotes in result.values():
            quotes.reverse()
        return result

    def benchmark_quotes(self, limit: int = 80, start_date: date | None = None) -> list[QuotePoint]:
        date_clause = f"AND trade_date >= '{start_date.isoformat()}'" if start_date else ""
        direction = "ASC" if start_date else "DESC"
        rows = self._clickhouse.execute(
            f"SELECT trade_date, close, updated_at FROM {self._clickhouse.database}.index_factor "
            f"FINAL WHERE index_code = '000300.CSI' {date_clause} ORDER BY trade_date {direction} "
            f"LIMIT {limit}"
        )
        quotes = [self._quote(row) for row in rows]
        return quotes if start_date is not None else list(reversed(quotes))

    @staticmethod
    def _quote(row: dict[str, Any] | Any) -> QuotePoint:
        trade_date = row["trade_date"]
        updated_at = row["updated_at"]
        normalized_trade_date = (
            trade_date.date()
            if isinstance(trade_date, datetime)
            else trade_date
            if isinstance(trade_date, date)
            else date.fromisoformat(str(trade_date))
        )
        return QuotePoint(
            trade_date=normalized_trade_date,
            close=float(row["close"]),
            updated_at=updated_at
            if isinstance(updated_at, datetime)
            else datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")),
        )
