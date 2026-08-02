"""Tushare ``stk_week_month_adj`` 周/月K线 Adapter（按 freq 分派两个独立模型）。"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lucking.integrations.tushare.client import TushareClient, TushareError, TushareTable
from lucking.integrations.tushare.daily_quote_provider import (
    _map_error,
    _split_security_id,
)
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence
from lucking.ports.market_data_common import (
    ProviderDeadlineExceededError,
    ProviderEmptyAggregateError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderResponseCappedError,
)
from lucking.ports.weekly_monthly_kline_provider import (
    KlineFreq,
    KlineRequest,
    ProviderKlineBatch,
    ProviderWeeklyMonthlyKline,
)

# 实测（2026-08-02 部署账户）：接口仅返回未复权 12 字段，无 qfq/hfq 复权价。
KLINE_FIELDS = (
    "ts_code",
    "trade_date",
    "freq",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "change",
    "pct_chg",
    "end_date",
)
_RETRY_DELAYS = (30.0, 120.0, 300.0)
_FREQ_PARAM = {KlineFreq.WEEK: "week", KlineFreq.MONTH: "month"}
_FREQ_BY_SOURCE = {"week": KlineFreq.WEEK, "month": KlineFreq.MONTH}

_PRICE_FIELDS = ("open", "high", "low", "close")


class TushareWeeklyMonthlyKlineProvider:
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
            raise ValueError("Tushare stk_week_month_adj 页面上限固定为 6000")
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

    def fetch_kline(
        self, request: KlineRequest, *, deadline: float
    ) -> ProviderKlineBatch:
        params: dict[str, Any] = {
            "freq": _FREQ_PARAM[request.freq],
            "trade_date": request.target_trade_date.strftime("%Y%m%d"),
        }
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
            page_params = dict(params)
            if self._pagination_enabled:
                page_params.update(limit=self._page_limit, offset=offset)
            table, attempts = self._call_page(
                page_params,
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

        records: list[ProviderWeeklyMonthlyKline] = []
        isolated: list[ProviderInvalidCandidate] = []
        for row in rows:
            try:
                if set(row) != set(KLINE_FIELDS):
                    raise ValueError("字段集合不精确")
                provider_id, venue, security_code = _split_security_id(row["ts_code"])
                row_freq = _FREQ_BY_SOURCE.get(str(row.get("freq") or "").strip())
                if row_freq is None:
                    raise ValueError("freq 不受支持")
                if row_freq is not request.freq:
                    raise ValueError("记录周期与请求周期不一致")
                trade_date = _parse_date(row["trade_date"])
                if trade_date > request.target_trade_date:
                    raise ValueError("周期最后交易日晚于请求交易日")
                prices: dict[str, Decimal] = {}
                missing_price: str | None = None
                for field in _PRICE_FIELDS:
                    parsed = _decimal_or_none(row[field])
                    if parsed is None:
                        missing_price = field
                        break
                    prices[field] = parsed
                if missing_price is not None:
                    isolated.append(
                        ProviderInvalidCandidate(
                            category="INVALID_FIELD",
                            safe_summary=f"未复权价格 {missing_price} 缺失，已隔离",
                            field_name=missing_price,
                            provider_security_id=provider_id,
                            venue_code=venue,
                            security_code=security_code,
                        )
                    )
                    continue
                end_date = _parse_end_date(row["end_date"], trade_date)
                records.append(
                    ProviderWeeklyMonthlyKline(
                        freq=request.freq,
                        trade_date=trade_date,
                        end_date=end_date,
                        provider_security_id=provider_id,
                        venue_code=venue,
                        security_code=security_code,
                        open=prices["open"],
                        high=prices["high"],
                        low=prices["low"],
                        close=prices["close"],
                        vol=_decimal(row["vol"], "vol"),
                        amount=_decimal(row["amount"], "amount"),
                        change=_decimal(row["change"], "change"),
                        pct_chg=_decimal(row["pct_chg"], "pct_chg"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderPayloadError(
                    self.provider_code, f"K线记录无效：{exc}"
                ) from exc
        return ProviderKlineBatch(
            provider_code=self.provider_code,
            freq=request.freq,
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
                        "stk_week_month_adj",
                        params=params,
                        fields=KLINE_FIELDS,
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


def _parse_date(value: Any) -> date:
    """来源 trade_date 实测返回 'YYYYMMDD'，兼容 ISO 带横线格式。"""
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").date()
        return date.fromisoformat(raw)
    raise ValueError("trade_date 缺失")


def _parse_end_date(value: Any, trade_date: date) -> date | None:
    """来源 end_date 实测返回 'YYYYMMDD'；与 trade_date 一致时为空。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        end = (
            datetime.strptime(raw, "%Y%m%d").date()
            if len(raw) == 8 and raw.isdigit()
            else date.fromisoformat(raw)
        )
        return None if end == trade_date else end
    raise ValueError("end_date 类型非法")


def _decimal(value: Any, field: str) -> Decimal:
    parsed = _decimal_or_none(value)
    if parsed is None:
        raise ValueError(f"{field} 为空或类型非法")
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
    """来源价格/量额实测返回 float/int，兼容字符串与 Decimal。"""
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError("价格字段不是有效数字") from exc
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    raise ValueError("价格字段类型非法")


def _page_digest(rows: tuple[Mapping[str, Any], ...]) -> str:
    serialized = json.dumps([dict(row) for row in rows], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()
