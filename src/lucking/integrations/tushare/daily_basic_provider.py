"""Tushare ``daily_basic`` 每日基本面指标 Adapter。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lucking.integrations.tushare.client import TushareClient, TushareError, TushareTable
from lucking.integrations.tushare.daily_quote_provider import (
    _map_error,
    _parse_date,
    _split_security_id,
)
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence
from lucking.ports.daily_basic_provider import (
    DailyBasicRequest,
    ProviderDailyBasic,
    ProviderDailyBasicBatch,
)
from lucking.ports.market_data_common import (
    ProviderDeadlineExceededError,
    ProviderEmptyAggregateError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderResponseCappedError,
)

# 来源 19 字段去掉与日线重复的 close（单表事实原则）。
DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "limit_status",
)
_RETRY_DELAYS = (30.0, 120.0, 300.0)


class TushareDailyBasicProvider:
    provider_code = "tushare"

    def __init__(
        self,
        client: TushareClient,
        *,
        page_limit: int = 6000,
        max_pages: int = 10,
        pagination_enabled: bool = False,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        if page_limit != 6000:
            raise ValueError("Tushare daily_basic 页面上限固定为 6000")
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

    def fetch_daily_basics(
        self, request: DailyBasicRequest, *, deadline: float
    ) -> ProviderDailyBasicBatch:
        trade_date = request.target_trade_date.strftime("%Y%m%d")
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
            params: dict[str, Any] = {"trade_date": trade_date}
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
                "market_data_page_completed",
                provider_code=self.provider_code,
                provider_page_count=page_count,
                provider_last_page_count=last_page_count,
                received_count=len(rows) + last_page_count,
            )
            if not self._pagination_enabled:
                if last_page_count == 0:
                    raise ProviderEmptyAggregateError(self.provider_code, "上游返回空数据")
                if last_page_count >= self._page_limit:
                    raise ProviderResponseCappedError(
                        self.provider_code, "响应达到行数上限且未验证续取完整"
                    )
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

        records: list[ProviderDailyBasic] = []
        isolated: list[ProviderInvalidCandidate] = []
        for row in rows:
            try:
                if set(row) != set(DAILY_BASIC_FIELDS):
                    raise ValueError("字段集合不精确")
                raw_ts_code = row.get("ts_code")
                raw_trade_date = row.get("trade_date")
                if not isinstance(raw_ts_code, str) or not raw_ts_code.strip():
                    isolated.append(
                        _identity_issue("ts_code", None)
                    )
                    continue
                if not isinstance(raw_trade_date, str) or not raw_trade_date.strip():
                    isolated.append(
                        _identity_issue("trade_date", raw_ts_code.strip())
                    )
                    continue
                provider_id, venue, security_code = _split_security_id(raw_ts_code)
                row_trade_date = _parse_date(raw_trade_date)
                if row_trade_date != request.target_trade_date:
                    raise ValueError("记录交易日与请求交易日不一致")
                records.append(
                    ProviderDailyBasic(
                        trade_date=row_trade_date,
                        provider_security_id=provider_id,
                        venue_code=venue,
                        security_code=security_code,
                        pe=_nullable_decimal(row["pe"]),
                        pe_ttm=_nullable_decimal(row["pe_ttm"]),
                        pb=_nullable_decimal(row["pb"]),
                        ps=_nullable_decimal(row["ps"]),
                        ps_ttm=_nullable_decimal(row["ps_ttm"]),
                        dv_ratio=_nullable_decimal(row["dv_ratio"]),
                        dv_ttm=_nullable_decimal(row["dv_ttm"]),
                        total_share=_nullable_decimal(row["total_share"]),
                        float_share=_nullable_decimal(row["float_share"]),
                        free_share=_nullable_decimal(row["free_share"]),
                        total_mv=_nullable_decimal(row["total_mv"]),
                        circ_mv=_nullable_decimal(row["circ_mv"]),
                        turnover_rate=_nullable_decimal(row["turnover_rate"]),
                        turnover_rate_f=_nullable_decimal(row["turnover_rate_f"]),
                        volume_ratio=_nullable_decimal(row["volume_ratio"]),
                        limit_status=_nullable_int(row["limit_status"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderPayloadError(
                    self.provider_code, f"基本面记录无效：{exc}"
                ) from exc
        return ProviderDailyBasicBatch(
            provider_code=self.provider_code,
            target_trade_date=request.target_trade_date,
            records=tuple(records),
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
            isolated=tuple(isolated),
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
                "market_data_provider_attempt_started",
                provider_code=self.provider_code,
                provider_request_count=request_no,
                provider_retry_count=retry_start + page_retries,
            )
            try:
                return (
                    self._client.call(
                        "daily_basic",
                        params=params,
                        fields=DAILY_BASIC_FIELDS,
                        allow_empty=True,
                    ),
                    page_retries + 1,
                )
            except TushareError as exc:
                mapped = _map_error(exc, request_no)
                global_retry = retry_start + page_retries
                self._event_sink(
                    "market_data_provider_attempt_failed",
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


def _identity_issue(field_name: str, raw_ts_code: str | None) -> ProviderInvalidCandidate:
    return ProviderInvalidCandidate(
        category="INVALID_FIELD",
        safe_summary="必需身份字段缺失，已隔离",
        field_name=field_name,
        provider_security_id=raw_ts_code,
    )


def _nullable_decimal(value: Any) -> Decimal | None:
    """来源指标字段实测返回 float/int，亏损空值为空串；None 表示来源未返回。"""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError("指标字段不是有效数字") from exc
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    raise ValueError("指标字段类型非法")


def _nullable_int(value: Any) -> int | None:
    """limit_status 实测返回 int；空值映射为 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValueError("limit_status 不是有效整数") from exc
    if isinstance(value, int):
        return value
    raise ValueError("limit_status 类型非法")


def _page_digest(rows: tuple[Mapping[str, Any], ...]) -> str:
    serialized = json.dumps([dict(row) for row in rows], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()
