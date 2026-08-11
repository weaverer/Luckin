from datetime import date

import pytest

from lucking.services.broker_recommendation_query import BrokerRecommendationQuery


class Repository:
    def __init__(self) -> None:
        self.arguments = None

    def list(self, **arguments):
        self.arguments = arguments
        return [object()], 1


def test_month_must_be_first_day_and_filters_are_forwarded() -> None:
    repository = Repository()
    service = BrokerRecommendationQuery(repository)
    with pytest.raises(ValueError):
        service.list(date(2026, 8, 2), None, None, 20, 0)
    page = service.list(date(2026, 8, 1), "中信证券", "stock-1", 20, 0)
    assert page.total == 1
    assert repository.arguments == {
        "recommendation_month": date(2026, 8, 1),
        "broker_name": "中信证券",
        "stock_id": "stock-1",
        "limit": 20,
        "offset": 0,
    }


def test_pagination_bounds_are_enforced() -> None:
    service = BrokerRecommendationQuery(Repository())
    with pytest.raises(ValueError):
        service.list(date(2026, 8, 1), None, None, 1001, 0)
    with pytest.raises(ValueError):
        service.list(date(2026, 8, 1), None, None, 20, -1)
