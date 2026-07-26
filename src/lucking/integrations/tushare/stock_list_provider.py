"""Tushare ``stock_basic`` adapter for the provider-neutral stock-list port."""

from __future__ import annotations

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
from lucking.ports.stock_list_provider import (
    ListingStatus,
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderStockList,
    ProviderStockRecord,
    ProviderUnavailableError,
    RetrievalEvidence,
    ScopeCode,
    StockListRequest,
    VenueCode,
)

STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "curr_type",
    "list_status",
    "list_date",
    "delist_date",
)
SEGMENTS = tuple(
    (exchange, status)
    for exchange in ("SSE", "SZSE", "BSE")
    for status in ("L", "D", "P", "G")
)
_VENUES = {
    "SSE": (VenueCode.SHANGHAI, ".SH"),
    "SZSE": (VenueCode.SHENZHEN, ".SZ"),
    "BSE": (VenueCode.BEIJING, ".BJ"),
}
_STATUSES = {
    "L": ListingStatus.ACTIVE,
    "D": ListingStatus.DELISTED,
    "P": ListingStatus.SUSPENDED,
    "G": ListingStatus.PENDING,
}
_RETRY_DELAYS = (30.0, 120.0, 300.0)


class TushareStockListProvider:
    provider_code = "tushare"

    def __init__(
        self,
        client: TushareClient,
        *,
        row_cap: int = 6000,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        self._client = client
        self._row_cap = row_cap
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink or (lambda _event, **_fields: None)

    def fetch_stock_list(
        self, request: StockListRequest, *, deadline: float
    ) -> ProviderStockList:
        if request.scope_code is not ScopeCode.CN_STOCK:
            raise ProviderRequestError(self.provider_code, "首期只支持 CN-S")
        records: list[ProviderStockRecord] = []
        completed = 0
        received = 0
        for segment_no, (exchange, status) in enumerate(SEGMENTS, start=1):
            table = self._call_segment(
                exchange, status, segment_no=segment_no, deadline=deadline
            )
            count = len(table.rows)
            received += count
            if count == self._row_cap:
                raise ProviderIncompleteError(
                    self.provider_code,
                    f"segment 恰好达到 {self._row_cap:,} 行，无法证明完整",
                    segment_no=segment_no,
                )
            if count > self._row_cap:
                raise ProviderPayloadError(
                    self.provider_code,
                    "segment 行数超过契约上限",
                    segment_no=segment_no,
                )
            records.extend(
                self._map_row(row, exchange, status, segment_no)
                for row in table.rows
            )
            completed += 1
        if not records:
            raise ProviderIncompleteError(self.provider_code, "12 个分区聚合结果为空")
        return ProviderStockList(
            self.provider_code,
            request.scope_code,
            tuple(records),
            RetrievalEvidence(len(SEGMENTS), completed, 0, received),
            self._now().astimezone(UTC),
        )

    def _call_segment(
        self,
        exchange: str,
        status: str,
        *,
        segment_no: int,
        deadline: float,
    ) -> TushareTable:
        attempt = 0
        while True:
            if self._monotonic() >= deadline:
                raise ProviderDeadlineExceededError(
                    self.provider_code, "Provider 获取超过整体截止时间", segment_no=segment_no
                )
            try:
                self._event_sink(
                    "stock_list_segment_attempt_started",
                    provider_code=self.provider_code,
                    segment_count=segment_no,
                    attempt_count=attempt + 1,
                )
                return self._client.call(
                    "stock_basic",
                    params={"exchange": exchange, "list_status": status},
                    fields=STOCK_BASIC_FIELDS,
                    allow_empty=True,
                )
            except TushareError as exc:
                mapped = _map_error(exc, segment_no)
                self._event_sink(
                    "stock_list_segment_attempt_failed",
                    provider_code=self.provider_code,
                    segment_count=segment_no,
                    attempt_count=attempt + 1,
                    error_category=mapped.category,
                    error_summary=mapped.summary,
                )
                if not mapped.retryable or attempt >= len(_RETRY_DELAYS):
                    raise mapped from exc
                delay = _RETRY_DELAYS[attempt]
                attempt += 1
                if self._monotonic() + delay >= deadline:
                    raise ProviderDeadlineExceededError(
                        self.provider_code,
                        "重试等待将超过整体截止时间",
                        segment_no=segment_no,
                    ) from exc
                self._sleep(delay)

    def _map_row(
        self,
        row: Mapping[str, Any],
        exchange: str,
        status: str,
        segment_no: int,
    ) -> ProviderStockRecord:
        try:
            if set(row) != set(STOCK_BASIC_FIELDS):
                raise ValueError("字段集合不精确")
            row_exchange = _required_text(row["exchange"], "exchange")
            row_status = _required_text(row["list_status"], "list_status")
            if row_exchange != exchange or row_status != status:
                raise ValueError("记录不属于请求分区")
            venue, suffix = _VENUES[row_exchange]
            listing_status = _STATUSES[row_status]
            provider_id = _required_text(row["ts_code"], "ts_code")
            if not provider_id.endswith(suffix):
                raise ValueError("ts_code 后缀与交易所不一致")
            security_code = _required_text(row["symbol"], "symbol")
            display_name = _required_text(row["name"], "name")
            currency = _required_text(row["curr_type"], "curr_type")
            if currency != "CNY":
                raise ValueError("未知币种")
            listed_on = _parse_date(row["list_date"], "list_date")
            delisted_on = _parse_date(row["delist_date"], "delist_date")
            if listing_status is not ListingStatus.PENDING and listed_on is None:
                raise ValueError("当前状态要求上市日期")
            if listing_status is ListingStatus.DELISTED and delisted_on is None:
                raise ValueError("退市状态要求退市日期")
            if listed_on and delisted_on and delisted_on < listed_on:
                raise ValueError("退市日期早于上市日期")
            return ProviderStockRecord(
                provider_id,
                venue,
                security_code,
                display_name,
                currency,
                listing_status,
                listed_on,
                delisted_on,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPayloadError(
                self.provider_code,
                f"segment 记录无效：{exc}",
                segment_no=segment_no,
            ) from exc


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 为空或类型非法")
    return value.strip()


def _parse_date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ValueError(f"{field} 不是 YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"{field} 不是有效日期") from exc


def _map_error(error: TushareError, segment_no: int) -> ProviderError:
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
        "tushare",
        error.summary,
        status_code=error.status_code,
        segment_no=segment_no,
    )
