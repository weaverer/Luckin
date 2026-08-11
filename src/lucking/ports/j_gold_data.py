"""J金股研究所需的规范数据读取端口。"""

from datetime import date
from typing import Protocol

from lucking.models.j_gold import QuotePoint, RecommendationFact


class JGoldDataReader(Protocol):
    def available_months(self) -> list[date]: ...

    def recommendations(self, start_month: date, end_month: date) -> list[RecommendationFact]: ...

    def stock_quotes(
        self, stock_id: str, limit: int = 80, start_date: date | None = None
    ) -> list[QuotePoint]: ...

    def stock_quotes_batch(
        self, stock_ids: list[str], limit: int = 400
    ) -> dict[str, list[QuotePoint]]: ...

    def benchmark_quotes(
        self, limit: int = 80, start_date: date | None = None
    ) -> list[QuotePoint]: ...
