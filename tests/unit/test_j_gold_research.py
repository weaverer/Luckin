from datetime import UTC, date, datetime, timedelta

from lucking.models.j_gold import QuotePoint, RecommendationFact, ResearchContext
from lucking.services.j_gold_research import MIN_BROKER_SAMPLES, JGoldResearchService


class MemoryJGoldReader:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.facts = [
            RecommendationFact(
                date(2026, 7, 1), "甲券商", "s1", "000001", "甲公司", "ACTIVE", "银行", now
            ),
            RecommendationFact(
                date(2026, 8, 1), "甲券商", "s1", "000001", "甲公司", "ACTIVE", "银行", now
            ),
            RecommendationFact(
                date(2026, 8, 1), "乙券商", "s1", "000001", "甲公司", "ACTIVE", "银行", now
            ),
            RecommendationFact(
                date(2026, 8, 1), "甲券商", "s2", "000002", "乙公司", "ACTIVE", "科技", now
            ),
        ]
        self.quotes = [
            QuotePoint(date(2026, 4, 1) + timedelta(days=i), 10 + i / 10, now) for i in range(80)
        ]
        self.batch_quote_calls = 0
        self.single_quote_calls = 0

    def available_months(self) -> list[date]:
        return [date(2026, 8, 1), date(2026, 7, 1)]

    def recommendations(self, start_month: date, end_month: date) -> list[RecommendationFact]:
        return [f for f in self.facts if start_month <= f.recommendation_month <= end_month]

    def stock_quotes(
        self, stock_id: str, limit: int = 80, start_date: date | None = None
    ) -> list[QuotePoint]:
        self.single_quote_calls += 1
        quotes = [q for q in self.quotes if start_date is None or q.trade_date >= start_date]
        return quotes[-limit:] if start_date is None else quotes[:limit]

    def stock_quotes_batch(
        self, stock_ids: list[str], limit: int = 400
    ) -> dict[str, list[QuotePoint]]:
        self.batch_quote_calls += 1
        return {stock_id: self.quotes[-limit:] for stock_id in stock_ids}

    def benchmark_quotes(self, limit: int = 80, start_date: date | None = None) -> list[QuotePoint]:
        quotes = [
            QuotePoint(q.trade_date, 10 + i / 20, q.updated_at) for i, q in enumerate(self.quotes)
        ]
        quotes = [q for q in quotes if start_date is None or q.trade_date >= start_date]
        return quotes[-limit:] if start_date is None else quotes[:limit]


def test_overview_uses_distinct_stock_and_brokers() -> None:
    reader = MemoryJGoldReader()
    result = JGoldResearchService(reader).research(ResearchContext(date(2026, 8, 1)))
    assert result.metrics["monthly_count"] == 2
    assert result.metrics["broker_count"] == 2
    assert len(result.metrics["warming_three_months"]) == 3
    first = next(item for item in result.items if item["stock"]["stock_id"] == "s1")
    assert first["broker_count"] == 2
    assert first["month_delta"] == 1
    assert first["score"] is not None
    assert reader.batch_quote_calls == 1
    assert reader.single_quote_calls == 0


def test_unknown_month_falls_back_to_latest_available() -> None:
    result = JGoldResearchService(MemoryJGoldReader()).research(ResearchContext(date(2026, 9, 1)))
    assert result.selected_month == date(2026, 8, 1)
    assert result.quality.status.value == "partial"


def test_metric_drilldown_filters_complete_radar_result_before_pagination() -> None:
    reader = MemoryJGoldReader()
    service = JGoldResearchService(reader)

    new_items = service.research(ResearchContext(date(2026, 8, 1), limit=1, radar_filter="new"))
    assert new_items.pagination["total"] == 1
    assert new_items.items[0]["stock"]["stock_id"] == "s2"

    warming_items = service.research(ResearchContext(date(2026, 8, 1), radar_filter="warming"))
    assert warming_items.pagination["total"] == 2
    assert {item["stock"]["stock_id"] for item in warming_items.items} == {"s1", "s2"}

    consensus_items = service.research(ResearchContext(date(2026, 8, 1), radar_filter="consensus"))
    assert consensus_items.pagination["total"] == 0
    assert consensus_items.items == []


def test_score_requires_two_components_and_threshold_is_explicit() -> None:
    assert (
        JGoldResearchService._score(
            {"consensus": 100.0, "warming": None, "continuity": None, "excess": None}
        )
        is None
    )
    assert (
        JGoldResearchService._score(
            {"consensus": 100.0, "warming": 0.0, "continuity": None, "excess": None}
        )
        == 54.5
    )
    assert MIN_BROKER_SAMPLES == 20


def test_missing_previous_month_does_not_turn_unknown_comparisons_into_zero() -> None:
    reader = MemoryJGoldReader()
    reader.available_months = lambda: [date(2026, 8, 1)]  # type: ignore[method-assign]
    result = JGoldResearchService(reader).research(ResearchContext(date(2026, 8, 1)))
    assert result.metrics["new_count"] is None
    assert result.metrics["warming_count"] is None
    assert all(item["month_delta"] is None for item in result.items)
    assert result.quality.status.value == "partial"


def test_duplicate_business_fact_is_counted_once_in_industry_consensus() -> None:
    reader = MemoryJGoldReader()
    duplicate = reader.facts[-1]
    reader.facts.append(duplicate)
    result = JGoldResearchService(reader).research(ResearchContext(date(2026, 8, 1)))
    technology = next(item for item in result.industries if item["industry"] == "科技")
    assert technology["recommendation_records"] == 1
    assert technology["stock_count"] == 1
    assert technology["broker_count"] == 1


def test_excess_return_uses_only_aligned_trading_dates() -> None:
    now = datetime.now(UTC)
    stock = [QuotePoint(date(2026, 1, 1) + timedelta(days=i), 100 + i, now) for i in range(22)]
    benchmark = [
        QuotePoint(date(2026, 1, 2) + timedelta(days=i), 100 + i / 2, now) for i in range(21)
    ]
    assert JGoldResearchService._excess(stock, benchmark, 20) is not None
    assert JGoldResearchService._excess(stock[:20], benchmark, 20) is None


def test_diffusion_keeps_missing_month_as_unknown() -> None:
    reader = MemoryJGoldReader()
    service = JGoldResearchService(reader)
    by_month = {date(2026, 8, 1): reader.facts[-2:]}
    points = service._diffusion(by_month, date(2026, 8, 1), datetime.now(UTC))
    assert points[-2]["stock_count"] is None
    assert points[-2]["quality"] == "insufficient"
    assert points[-1]["month_delta"] is None


def test_broker_ability_uses_month_anchored_samples_and_twenty_sample_gate() -> None:
    reader = MemoryJGoldReader()
    now = datetime.now(UTC)
    month = date(2026, 4, 1)
    facts = [
        RecommendationFact(
            month,
            "样本券商",
            f"s{i}",
            f"{600000 + i}",
            f"样本{i}",
            "ACTIVE",
            "行业",
            now,
        )
        for i in range(20)
    ]
    service = JGoldResearchService(reader)
    result = service._broker_ability(
        {month: facts},
        reader.benchmark_quotes(400),
        reader.stock_quotes_batch([fact.stock_id for fact in facts]),
        now,
    )
    item = result[0]
    assert item["sample_count"] == 20
    assert item["quality"] == "ready"
    assert item["grade"] is not None
    assert item["return_basis"].startswith("推荐月首")
