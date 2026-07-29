from datetime import date
from typing import Any

import pytest

from lucking.integrations.tushare.broker_recommendation_provider import (
    BROKER_RECOMMEND_FIELDS,
    TushareBrokerRecommendationProvider,
)
from lucking.integrations.tushare.client import TushareError, TushareErrorCategory, TushareTable
from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationRequest,
    ProviderIncompleteError,
    VenueCode,
)


class FakeClient:
    def __init__(self, pages: list[list[list[str]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any], tuple[str, ...]]] = []

    def call(
        self,
        api_name: str,
        *,
        params: dict[str, Any],
        fields: tuple[str, ...],
        allow_empty: bool = False,
    ) -> TushareTable:
        self.calls.append((api_name, params, fields))
        items = self.pages.pop(0)
        return TushareTable(
            fields,
            tuple(dict(zip(fields, row, strict=True)) for row in items),
        )


def _rows(start: int, count: int, suffix: str = ".SH") -> list[list[str]]:
    return [
        ["202607", " 券商\u3000A ", f"{index:06d}{suffix}", f"股票{index}"]
        for index in range(start, start + count)
    ]


def test_adapter_only_calls_broker_recommend_and_four_fields() -> None:
    client = FakeClient([_rows(1, 1, ".BJ")])
    batch = TushareBrokerRecommendationProvider(client, monotonic=lambda: 0.0).fetch_month(
        BrokerRecommendationRequest(date(2026, 7, 1)), deadline=10
    )
    assert client.calls == [("broker_recommend", {"month": "202607"}, BROKER_RECOMMEND_FIELDS)]
    assert batch.records[0].venue_code is VenueCode.BEIJING
    assert batch.records[0].security_code == "000001"


def test_verified_pagination_fetches_until_short_page() -> None:
    client = FakeClient([_rows(0, 1000), _rows(1000, 1000), _rows(2000, 500)])
    batch = TushareBrokerRecommendationProvider(
        client, pagination_enabled=True, monotonic=lambda: 0.0
    ).fetch_month(BrokerRecommendationRequest(date(2026, 7, 1)), deadline=10)
    assert len(batch.records) == 2500
    assert [call[1]["offset"] for call in client.calls] == [0, 1000, 2000]
    assert all(call[1]["limit"] == 1000 for call in client.calls)
    assert batch.evidence.page_count == 3
    assert batch.evidence.last_page_count == 500


def test_unverified_full_page_and_repeated_full_page_fail_closed() -> None:
    with pytest.raises(ProviderIncompleteError):
        TushareBrokerRecommendationProvider(
            FakeClient([_rows(0, 1000)]), monotonic=lambda: 0.0
        ).fetch_month(BrokerRecommendationRequest(date(2026, 7, 1)), deadline=10)


def test_retry_budget_and_backoff_are_shared_across_pages() -> None:
    class RetryClient(FakeClient):
        def __init__(self) -> None:
            super().__init__([_rows(0, 1000), _rows(1000, 1)])
            self.failures = 3

        def call(
            self,
            api_name: str,
            *,
            params: dict[str, Any],
            fields: tuple[str, ...],
            allow_empty: bool = False,
        ) -> TushareTable:
            if self.failures:
                self.failures -= 1
                raise TushareError(TushareErrorCategory.NETWORK, "网络错误")
            return super().call(api_name, params=params, fields=fields, allow_empty=allow_empty)

    delays: list[float] = []
    batch = TushareBrokerRecommendationProvider(
        RetryClient(),
        pagination_enabled=True,
        monotonic=lambda: 0.0,
        sleep=delays.append,
    ).fetch_month(BrokerRecommendationRequest(date(2026, 7, 1)), deadline=1000)
    assert delays == [30.0, 120.0, 300.0]
    assert batch.evidence.retry_count == 3
    repeated = _rows(0, 1000)
    with pytest.raises(ProviderIncompleteError):
        TushareBrokerRecommendationProvider(
            FakeClient([repeated, repeated]),
            pagination_enabled=True,
            monotonic=lambda: 0.0,
        ).fetch_month(BrokerRecommendationRequest(date(2026, 7, 1)), deadline=10)
