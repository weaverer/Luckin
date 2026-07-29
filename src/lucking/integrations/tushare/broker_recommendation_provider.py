"""Tushare ``broker_recommend`` adapter."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from lucking.integrations.tushare.client import (
    TushareClient,
    TushareError,
    TushareErrorCategory,
    TushareTable,
)
from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationRequest,
    ProviderAuthenticationError,
    ProviderBrokerRecommendation,
    ProviderBrokerRecommendationBatch,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderUnavailableError,
    RetrievalEvidence,
    VenueCode,
)

BROKER_RECOMMEND_FIELDS = ("month", "broker", "ts_code", "name")
_RETRY_DELAYS = (30.0, 120.0, 300.0)
_VENUES = {
    ".SH": VenueCode.SHANGHAI,
    ".SZ": VenueCode.SHENZHEN,
    ".BJ": VenueCode.BEIJING,
}


class TushareBrokerRecommendationProvider:
    provider_code = "tushare"

    def __init__(
        self,
        client: TushareClient,
        *,
        page_limit: int = 1000,
        max_pages: int = 100,
        pagination_enabled: bool = False,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        if page_limit != 1000:
            raise ValueError("Tushare broker_recommend 页面上限固定为 1000")
        if max_pages <= 0:
            raise ValueError("max_pages 必须大于 0")
        self._client = client
        self._page_limit = page_limit
        self._max_pages = max_pages
        self._pagination_enabled = pagination_enabled
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink or (lambda _event, **_fields: None)

    def fetch_month(
        self, request: BrokerRecommendationRequest, *, deadline: float
    ) -> ProviderBrokerRecommendationBatch:
        month = request.target_month.strftime("%Y%m")
        rows: list[Mapping[str, Any]] = []
        seen_pages: set[str] = set()
        request_count = 0
        retry_count = 0
        page_count = 0
        last_page_count = 0
        offset = 0

        while True:
            if page_count >= self._max_pages:
                raise ProviderIncompleteError(self.provider_code, "分页超过最大页数")
            params: dict[str, Any] = {"month": month}
            if self._pagination_enabled:
                params.update(limit=self._page_limit, offset=offset)
            table, attempts = self._call_page(
                params,
                request_no=page_count + 1,
                deadline=deadline,
                retry_start=retry_count,
            )
            request_count += attempts
            retry_count += attempts - 1
            page_count += 1
            last_page_count = len(table.rows)
            self._event_sink(
                "broker_recommendation_page_completed",
                provider_code=self.provider_code,
                provider_page_count=page_count,
                provider_last_page_count=last_page_count,
                received_count=len(rows) + last_page_count,
            )
            if not self._pagination_enabled:
                if last_page_count == 0:
                    raise ProviderIncompleteError(self.provider_code, "上游返回空首屏")
                if last_page_count >= self._page_limit:
                    raise ProviderIncompleteError(self.provider_code, "未验证分页的响应达到上限")
                rows.extend(table.rows)
                break

            if last_page_count == self._page_limit:
                digest = _page_digest(table.rows)
                if digest in seen_pages:
                    raise ProviderIncompleteError(self.provider_code, "检测到重复整页")
                seen_pages.add(digest)
                rows.extend(table.rows)
                new_offset = offset + self._page_limit
                if new_offset <= offset:
                    raise ProviderIncompleteError(self.provider_code, "分页位置未前进")
                offset = new_offset
                continue
            rows.extend(table.rows)
            break

        if not rows:
            raise ProviderIncompleteError(self.provider_code, "上游返回空数据")
        records = tuple(self._map_row(row, request.target_month) for row in rows)
        return ProviderBrokerRecommendationBatch(
            provider_code=self.provider_code,
            target_month=request.target_month,
            records=records,
            evidence=RetrievalEvidence(
                request_count=request_count,
                completed_request_count=request_count,
                retry_count=retry_count,
                page_count=page_count,
                page_limit=self._page_limit,
                last_page_count=last_page_count,
                received_count=len(rows),
                pagination_enabled=self._pagination_enabled,
                continuation_exhausted=True,
                repeated_page_detected=False,
            ),
            acquired_at=self._now().astimezone(UTC),
        )

    def _call_page(
        self,
        params: Mapping[str, Any],
        *,
        request_no: int,
        deadline: float,
        retry_start: int,
    ) -> tuple[TushareTable, int]:
        page_retries = 0
        while True:
            if self._monotonic() >= deadline:
                raise ProviderDeadlineExceededError(
                    self.provider_code, "Provider 获取超过整体截止时间"
                )
            self._event_sink(
                "broker_recommendation_provider_attempt_started",
                provider_code=self.provider_code,
                provider_request_count=request_no,
                provider_retry_count=retry_start + page_retries,
            )
            try:
                return (
                    self._client.call(
                        "broker_recommend",
                        params=params,
                        fields=BROKER_RECOMMEND_FIELDS,
                        allow_empty=True,
                    ),
                    page_retries + 1,
                )
            except TushareError as exc:
                mapped = _map_error(exc, request_no)
                global_retry = retry_start + page_retries
                self._event_sink(
                    "broker_recommendation_provider_attempt_failed",
                    provider_code=self.provider_code,
                    provider_request_count=request_no,
                    provider_retry_count=global_retry,
                    error_category=mapped.category,
                    error_summary=mapped.summary,
                )
                if not mapped.retryable or global_retry >= len(_RETRY_DELAYS):
                    raise mapped from exc
                delay = max(_RETRY_DELAYS[global_retry], exc.retry_after_seconds or 0.0)
                page_retries += 1
                if self._monotonic() + delay >= deadline:
                    raise ProviderDeadlineExceededError(
                        self.provider_code, "重试等待将超过整体截止时间"
                    ) from exc
                self._sleep(delay)

    def _map_row(self, row: Mapping[str, Any], target_month: date) -> ProviderBrokerRecommendation:
        try:
            if set(row) != set(BROKER_RECOMMEND_FIELDS):
                raise ValueError("字段集合不精确")
            raw_month = _required(row["month"], "month")
            if raw_month != target_month.strftime("%Y%m"):
                raise ValueError("记录月份与请求月份不一致")
            broker = _required(row["broker"], "broker")
            provider_id = _required(row["ts_code"], "ts_code")
            name = _required(row["name"], "name")
            suffix = provider_id[-3:]
            venue = _VENUES.get(suffix)
            if venue is None:
                raise ValueError("ts_code 后缀不受支持")
            security_code = provider_id[:-3]
            if not security_code:
                raise ValueError("证券代码为空")
            return ProviderBrokerRecommendation(
                target_month, broker, provider_id, venue, security_code, name
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPayloadError(self.provider_code, f"推荐记录无效：{exc}") from exc


def _required(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 为空或类型非法")
    return value.strip()


def _page_digest(rows: tuple[Mapping[str, Any], ...]) -> str:
    serialized = json.dumps([dict(row) for row in rows], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _map_error(error: TushareError, request_no: int) -> ProviderError:
    classes: dict[TushareErrorCategory, type[ProviderError]] = {
        TushareErrorCategory.NETWORK: ProviderUnavailableError,
        TushareErrorCategory.UPSTREAM_UNAVAILABLE: ProviderUnavailableError,
        TushareErrorCategory.RATE_LIMITED: ProviderRateLimitedError,
        TushareErrorCategory.QUOTA_EXHAUSTED: ProviderQuotaExceededError,
        TushareErrorCategory.AUTHENTICATION: ProviderAuthenticationError,
        TushareErrorCategory.BAD_REQUEST: ProviderRequestError,
        TushareErrorCategory.UPSTREAM_BUSINESS: ProviderRequestError,
        TushareErrorCategory.INVALID_PAYLOAD: ProviderPayloadError,
        TushareErrorCategory.EMPTY_PAYLOAD: ProviderPayloadError,
    }
    return classes[error.category](
        "tushare", error.summary, status_code=error.status_code, request_no=request_no
    )
