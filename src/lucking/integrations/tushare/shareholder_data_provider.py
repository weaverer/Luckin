"""Tushare 股东数据三接口 Adapter（top10_holders / top10_floatholders /
stk_holdernumber）。

供应商细节（字段名、限流档位、分页参数、错误码）只存在于本模块；业务代码
只依赖 ``lucking.ports.shareholder_data_common`` 与规范模型。设计要点
（research 决策 1/4/5，2026-08-05 部署账户实测确认）：
- 按公告日**全市场**查询（不传 ts_code，文档标注必填不准确）；
- 单次上限 6,000 行，`has_more/offset` 分页续取至 `has_more=False`
  （响应信封级标志，TushareTable.has_more 透传）；
- 空响应（单公告日 0 行）属正常披露节奏，返回空批而不是错误
  （spec FR-014/边界情况修订）；
- 共享 ``RateLimiter``（每分钟 ≤ 400 次，最小间隔 150 毫秒），
  三提取方法共用；重试退避 30/120/300 秒 ≤ 3 次，受 deadline 约束。
"""

from __future__ import annotations

import hashlib
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
from lucking.integrations.tushare.rate_limiter import RateLimiter, Throttle
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode
from lucking.models.shareholder_data import (
    PROVIDER_HOLDER_COUNT_FIELDS,
    PROVIDER_TOP10_HOLDER_FIELDS,
    ProviderShareholderBatch,
    ProviderShareholderCountBatch,
    ProviderShareholderCountRecord,
    ProviderShareholderRecord,
    ShareholderDataRequest,
)
from lucking.ports.market_data_common import (
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderIncompleteError,
    ProviderPayloadError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderResponseCappedError,
    ProviderUnavailableError,
)

_RETRY_DELAYS = (30.0, 120.0, 300.0)
_VENUES = {
    ".SH": VenueCode.SHANGHAI,
    ".SZ": VenueCode.SHENZHEN,
    ".BJ": VenueCode.BEIJING,
}


class TushareShareholderDataProvider:
    provider_code = "tushare"

    def __init__(
        self,
        client: TushareClient,
        *,
        page_limit: int = 6000,
        rate_per_minute: int = 400,
        max_pages: int = 20,
        limiter: Throttle | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        if page_limit != 6000:
            raise ValueError("Tushare 股东数据单次上限固定为 6000（实测）")
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute 必须大于 0")
        if max_pages <= 0:
            raise ValueError("max_pages 必须大于 0")
        self._client = client
        self._page_limit = page_limit
        self._max_pages = max_pages
        # 账户级限流预算（400/min 三接口合计）：优先注入分布式限流器
        # （Registry 组装 RedisRateLimiter），未注入时用进程级 RateLimiter
        # 兜底（测试与直接构造路径，research 决策 4 修订）。
        self._limiter = limiter or RateLimiter(
            rate_per_minute=rate_per_minute,
            monotonic=monotonic,
            sleep=sleep,
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink or (lambda _event, **_fields: None)

    def fetch_top10_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch:
        _validate_request(request, "TOP10")
        records, evidence, isolated = self._fetch_pages(
            request,
            deadline,
            api_name="top10_holders",
            fields=PROVIDER_TOP10_HOLDER_FIELDS,
            params_for=_ann_date_params,
            map_row=_map_holder_row,
        )
        return ProviderShareholderBatch(
            provider_code=self.provider_code,
            request_date=request.date,
            records=tuple(records),
            evidence=evidence,
            acquired_at=self._now().astimezone(UTC),
            isolated=tuple(isolated),
        )

    def fetch_top10_float_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch:
        _validate_request(request, "TOP10_FLOAT")
        records, evidence, isolated = self._fetch_pages(
            request,
            deadline,
            api_name="top10_floatholders",
            fields=PROVIDER_TOP10_HOLDER_FIELDS,
            params_for=_ann_date_params,
            map_row=_map_holder_row,
        )
        return ProviderShareholderBatch(
            provider_code=self.provider_code,
            request_date=request.date,
            records=tuple(records),
            evidence=evidence,
            acquired_at=self._now().astimezone(UTC),
            isolated=tuple(isolated),
        )

    def fetch_holder_count(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderCountBatch:
        _validate_request(request, "HOLDER_COUNT")
        records, evidence, isolated = self._fetch_pages(
            request,
            deadline,
            api_name="stk_holdernumber",
            fields=PROVIDER_HOLDER_COUNT_FIELDS,
            params_for=_ann_range_params,
            map_row=_map_count_row,
        )
        return ProviderShareholderCountBatch(
            provider_code=self.provider_code,
            request_date=request.date,
            records=tuple(records),
            evidence=evidence,
            acquired_at=self._now().astimezone(UTC),
            isolated=tuple(isolated),
        )

    def _fetch_pages(
        self,
        request: ShareholderDataRequest,
        deadline: float,
        *,
        api_name: str,
        fields: tuple[str, ...],
        params_for: Callable[[date], dict[str, str]],
        map_row: Callable[[Mapping[str, Any], date], Any],
    ) -> tuple[list[Any], RetrievalEvidence, list[ProviderInvalidCandidate]]:
        day = request.date
        pages: list[TushareTable] = []
        page_digests: list[str] = []
        repeated_page = False
        total_requests = 0
        total_retries = 0
        while len(pages) < self._max_pages:
            params: dict[str, Any] = {"limit": self._page_limit}
            if pages:
                params["offset"] = self._page_limit * len(pages)
            params.update(params_for(day))
            table, page_retries = self._call_page(
                params,
                api_name=api_name,
                fields=fields,
                request_no=len(pages) + 1,
                deadline=deadline,
                retry_start=total_retries,
            )
            total_requests += page_retries + 1
            total_retries += page_retries
            digest = _page_digest(table.rows)
            if digest and digest in page_digests:
                repeated_page = True
                break
            page_digests.append(digest)
            pages.append(table)
            if not table.has_more:
                break
        if repeated_page:
            # 重复批次/位置不前进优先判定为不完整（ED-003），与触顶区分
            raise ProviderIncompleteError(
                self.provider_code, "检出重复批次或分页位置未前进"
            )
        if pages and pages[-1].has_more:
            raise ProviderResponseCappedError(
                self.provider_code, "响应超过最大页数仍未穷尽（has_more 未收敛）"
            )
        records: list[Any] = []
        isolated: list[ProviderInvalidCandidate] = []
        for table in pages:
            for row in table.rows:
                try:
                    records.append(map_row(row, day))
                except _InvalidRow as exc:
                    isolated.append(
                        ProviderInvalidCandidate(
                            category="INVALID_FIELD",
                            safe_summary=exc.safe_summary,
                            provider_security_id=exc.provider_security_id,
                            security_code=exc.provider_security_id,
                        )
                    )
        last_page_count = len(pages[-1].rows) if pages else 0
        received_count = sum(len(table.rows) for table in pages)
        evidence = RetrievalEvidence(
            request_count=total_requests,
            completed_request_count=total_requests,
            retry_count=total_retries,
            page_count=len(pages),
            page_limit=self._page_limit,
            last_page_count=last_page_count,
            received_count=received_count,
            pagination_enabled=True,
            continuation_exhausted=bool(pages) and not pages[-1].has_more,
            repeated_page_detected=repeated_page,
        )
        return records, evidence, isolated

    def _call_page(
        self,
        params: Mapping[str, Any],
        *,
        api_name: str,
        fields: tuple[str, ...],
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
                "shareholder_data_provider_attempt_started",
                provider_code=self.provider_code,
                provider_request_count=request_no,
                provider_retry_count=retry_start + page_retries,
            )
            try:
                return (
                    self._client.call(
                        api_name,
                        params=params,
                        fields=fields,
                        allow_empty=True,
                    ),
                    page_retries,
                )
            except TushareError as exc:
                mapped = _map_error(exc, request_no)
                global_retry = retry_start + page_retries
                self._event_sink(
                    "shareholder_data_provider_attempt_failed",
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


class _InvalidRow(Exception):
    """行级可隔离错误（结构性错误整批失败，行级错误单条隔离）。"""

    def __init__(self, provider_security_id: str, safe_summary: str) -> None:
        self.provider_security_id = provider_security_id
        self.safe_summary = safe_summary
        super().__init__(safe_summary)


def _ann_date_params(day: date) -> dict[str, str]:
    return {"ann_date": day.strftime("%Y%m%d")}


def _ann_range_params(day: date) -> dict[str, str]:
    raw = day.strftime("%Y%m%d")
    return {"start_date": raw, "end_date": raw}


def _validate_request(request: ShareholderDataRequest, expected: str) -> None:
    if request.holder_kind != expected:
        raise ValueError(f"holder_kind 与提取方法不一致：{request.holder_kind} != {expected}")


def _map_holder_row(row: Mapping[str, Any], day: date) -> ProviderShareholderRecord:
    try:
        if set(row) != set(PROVIDER_TOP10_HOLDER_FIELDS):
            raise ValueError("字段集合不精确")
        ann_date = _parse_date(row["ann_date"])
        if ann_date != day:
            raise ValueError("记录公告日期与请求日期不一致")
        end_date = _parse_date(row["end_date"])
        provider_id, venue, security_code = _split_security_id(row["ts_code"])
        holder_name = row["holder_name"]
        if not isinstance(holder_name, str) or not holder_name.strip():
            raise ValueError("holder_name 缺失")
        return ProviderShareholderRecord(
            provider_security_id=provider_id,
            venue_code=venue,
            security_code=security_code,
            ann_date=ann_date,
            end_date=end_date,
            holder_name=holder_name.strip(),
            hold_amount=_optional_decimal(row["hold_amount"], "hold_amount"),
            hold_ratio=_optional_decimal(row["hold_ratio"], "hold_ratio"),
            hold_float_ratio=_optional_decimal(row["hold_float_ratio"], "hold_float_ratio"),
            hold_change=_optional_decimal(row["hold_change"], "hold_change"),
            holder_type=_optional_text(row["holder_type"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _InvalidRow(
            _safe_ts_code(row.get("ts_code")), f"前十大股东记录无效：{exc}"
        ) from exc


def _map_count_row(row: Mapping[str, Any], day: date) -> ProviderShareholderCountRecord:
    try:
        if set(row) != set(PROVIDER_HOLDER_COUNT_FIELDS):
            raise ValueError("字段集合不精确")
        ann_date = _parse_date(row["ann_date"])
        if ann_date != day:
            raise ValueError("记录公告日期与请求日期不一致")
        end_date = _parse_date(row["end_date"])
        provider_id, venue, security_code = _split_security_id(row["ts_code"])
        return ProviderShareholderCountRecord(
            provider_security_id=provider_id,
            venue_code=venue,
            security_code=security_code,
            ann_date=ann_date,
            end_date=end_date,
            holder_num=_optional_int(row["holder_num"], "holder_num"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _InvalidRow(
            _safe_ts_code(row.get("ts_code")), f"股东人数记录无效：{exc}"
        ) from exc


def _safe_ts_code(value: Any) -> str:
    return str(value) if isinstance(value, str) else "<unknown>"


def _split_security_id(value: Any) -> tuple[str, VenueCode, str]:
    """ts_code（如 600000.SH / 000001.SZ / 830799.BJ）→ 标识、交易场所、代码。"""
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
    """解析来源日期：'YYYYMMDD' 或 ISO 带横线格式。"""
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").date()
        return date.fromisoformat(raw)
    raise ValueError("日期缺失")


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field} 不是有效数字") from exc
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    raise ValueError(f"{field} 为空或类型非法")


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 不是有效整数")
    if isinstance(value, (int, float, Decimal)):
        return int(value)
    raise ValueError(f"{field} 不是有效整数")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return str(value)


def _page_digest(rows: tuple[Mapping[str, Any], ...]) -> str:
    """整页首行值摘要（重复页/位置不前进检测）。"""
    if not rows:
        return ""
    payload = "|".join(f"{key}={value}" for key, value in sorted(rows[0].items()))
    return hashlib.sha256(payload.encode()).hexdigest()


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
