"""Tushare ``stk_factor_pro`` 股票技术面因子 Adapter。

供应商细节（字段名、限流档位、错误码）只存在于本模块；业务代码只依赖
``lucking.ports.stock_factor_common`` 与规范模型。请求前必须经过共享
``RateLimiter`` 节流（每分钟 ≤ 30 次，spec FR-005）。
字段白名单 ``STOCK_FACTOR_FIELDS`` 经部署账户实测校准（2026-08-04，
trade_date=20260803 全量 5529 行；价格字段无 _bfq 变体，指标三变体齐备，
research 待验证项 2 / T008）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lucking.integrations.tushare.client import (
    TushareClient,
    TushareError,
    TushareErrorCategory,
    TushareTable,
)
from lucking.integrations.tushare.rate_limiter import RateLimiter
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode
from lucking.models.stock_factor import (
    DAY_COUNT_SET,
    STOCK_FACTOR_FIELDS,
    ProviderStockFactorBatch,
    ProviderStockFactorRecord,
    StockFactorRequest,
)
from lucking.ports.market_data_common import (
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderEmptyAggregateError,
    ProviderError,
    ProviderPayloadError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderResponseCappedError,
    ProviderUnavailableError,
)

# 身份列 + 行情锚点 close + 白名单数据列 = 来源返回字段全集
# （实测 261 字段 = ts_code/trade_date + close + 258 数据字段）。
PROVIDER_STOCK_FACTOR_FIELDS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "close",
) + STOCK_FACTOR_FIELDS

_RETRY_DELAYS = (30.0, 120.0, 300.0)
_VENUES = {
    ".SH": VenueCode.SHANGHAI,
    ".SZ": VenueCode.SHENZHEN,
    ".BJ": VenueCode.BEIJING,
}


class _NoQuoteRow(Exception):
    """该股票当日无行情（close 缺失）：按单条记录隔离，不阻断整批。"""

    def __init__(self, provider_security_id: str) -> None:
        self.provider_security_id = provider_security_id
        super().__init__(provider_security_id)


class TushareStockFactorProvider:
    provider_code = "tushare"

    def __init__(
        self,
        client: TushareClient,
        *,
        page_limit: int = 10000,
        rate_per_minute: int = 30,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        if page_limit != 10000:
            raise ValueError("Tushare stk_factor_pro 单次上限固定为 10000")
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute 必须大于 0")
        self._client = client
        self._page_limit = page_limit
        self._limiter = RateLimiter(
            rate_per_minute=rate_per_minute,
            monotonic=monotonic,
            sleep=sleep,
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink or (lambda _event, **_fields: None)

    def fetch_stock_factors(
        self, request: StockFactorRequest, *, deadline: float
    ) -> ProviderStockFactorBatch:
        params: dict[str, Any] = {"trade_date": request.target_trade_date.strftime("%Y%m%d")}
        table, attempts = self._call_page(
            params,
            request_no=1,
            deadline=deadline,
            retry_start=0,
        )
        last_page_count = len(table.rows)
        if last_page_count == 0:
            raise ProviderEmptyAggregateError(self.provider_code, "上游返回空数据")
        if last_page_count >= self._page_limit:
            raise ProviderResponseCappedError(
                self.provider_code, "响应达到行数上限且未验证续取完整"
            )
        records: list[ProviderStockFactorRecord] = []
        isolated: list[ProviderInvalidCandidate] = []
        for row in table.rows:
            try:
                records.append(self._map_row(row, request.target_trade_date))
            except _NoQuoteRow as exc:
                # 股票当日无行情（close 缺失，如停牌/新股缺因子）属正常业务结果
                # （spec FR-014/ED-004）：逐条隔离计数，不阻断同交易日其他有效数据
                isolated.append(
                    ProviderInvalidCandidate(
                        category="INVALID_FIELD",
                        safe_summary="股票当日无行情（收盘价缺失）",
                        provider_security_id=exc.provider_security_id,
                        security_code=exc.provider_security_id,
                    )
                )
        return ProviderStockFactorBatch(
            provider_code=self.provider_code,
            target_trade_date=request.target_trade_date,
            records=tuple(records),
            evidence=RetrievalEvidence(
                request_count=attempts,
                completed_request_count=attempts,
                retry_count=attempts - 1,
                page_count=1,
                page_limit=self._page_limit,
                last_page_count=last_page_count,
                received_count=len(table.rows),
                pagination_enabled=False,
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
            # 节流：任意 60 秒窗口 ≤ rate 次（重试请求同样受约束）
            self._limiter.wait_before_call()
            if self._monotonic() >= deadline:
                raise ProviderDeadlineExceededError(
                    self.provider_code, "Provider 获取超过整体截止时间"
                )
            self._event_sink(
                "stock_factor_provider_attempt_started",
                provider_code=self.provider_code,
                provider_request_count=request_no,
                provider_retry_count=retry_start + page_retries,
            )
            try:
                return (
                    self._client.call(
                        "stk_factor_pro",
                        params=params,
                        fields=PROVIDER_STOCK_FACTOR_FIELDS,
                        allow_empty=True,
                    ),
                    page_retries + 1,
                )
            except TushareError as exc:
                mapped = _map_error(exc, request_no)
                global_retry = retry_start + page_retries
                self._event_sink(
                    "stock_factor_provider_attempt_failed",
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

    def _map_row(self, row: Mapping[str, Any], target: date) -> ProviderStockFactorRecord:
        try:
            if set(row) != set(PROVIDER_STOCK_FACTOR_FIELDS):
                raise ValueError("字段集合不精确")
            trade_date = _parse_date(row["trade_date"])
            if trade_date != target:
                raise ValueError("记录交易日与请求交易日不一致")
            provider_id, venue, security_code = _split_security_id(row["ts_code"])
            try:
                close = _decimal(row["close"], "close")
            except ValueError:
                # close 缺失 → 该股票当日无行情，单条隔离
                raise _NoQuoteRow(provider_id) from None
            values: dict[str, Any] = {
                name: (
                    _optional_int(row[name], name)
                    if name in DAY_COUNT_SET
                    else _optional_decimal(row[name], name)
                )
                for name in STOCK_FACTOR_FIELDS
            }
            return ProviderStockFactorRecord(
                trade_date=trade_date,
                provider_security_id=provider_id,
                venue_code=venue,
                security_code=security_code,
                close=close,
                values=values,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPayloadError(
                self.provider_code, f"股票技术面因子记录无效：{exc}"
            ) from exc


def _split_security_id(value: Any) -> tuple[str, VenueCode, str]:
    """ts_code（如 600152.SH / 000001.SZ / 830799.BJ）→ 标识、交易场所、代码。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ts_code 缺失")
    provider_id = value.strip()
    suffix = provider_id[-3:]
    venue = _VENUES.get(suffix)
    if venue is None:
        raise ValueError("ts_code 后缀不受支持")
    security_code = provider_id[:-3]
    if not security_code:
        raise ValueError("证券代码为空")
    return provider_id, venue, security_code


def _parse_date(value: Any) -> date:
    """解析来源 trade_date：'YYYYMMDD' 或 ISO 带横线格式。"""
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").date()
        return date.fromisoformat(raw)
    raise ValueError("trade_date 缺失")


def _decimal(value: Any, field: str) -> Decimal:
    """行情锚点数字必须非空；来源实测返回 float/int，兼容字符串。"""
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{field} 为空或类型非法")
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field} 不是有效数字") from exc
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    raise ValueError(f"{field} 为空或类型非法")


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    """因子/估值数字允许为空（来源未返回时以 None 保存，区别于无效记录）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _decimal(value, field)


def _optional_int(value: Any, field: str) -> int | None:
    """整数因子（updays/downdays/lowdays/topdays）允许为空；来源返回 float。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 不是有效整数")
    if isinstance(value, (int, float, Decimal)):
        return int(value)
    raise ValueError(f"{field} 不是有效整数")


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
        TushareErrorCategory.EMPTY_PAYLOAD: ProviderEmptyAggregateError,
    }
    return classes[error.category](
        "tushare", error.summary, status_code=error.status_code, request_no=request_no
    )
