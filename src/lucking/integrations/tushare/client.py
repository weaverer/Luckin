"""Reusable synchronous client for the Tushare HTTP envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

import httpx


class TushareErrorCategory(StrEnum):
    NETWORK = "NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    AUTHENTICATION = "AUTHENTICATION"
    BAD_REQUEST = "BAD_REQUEST"
    UPSTREAM_BUSINESS = "UPSTREAM_BUSINESS"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    EMPTY_PAYLOAD = "EMPTY_PAYLOAD"


class TushareError(RuntimeError):
    def __init__(
        self,
        category: TushareErrorCategory,
        summary: str,
        *,
        status_code: int | None = None,
        provider_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.category = category
        self.summary = summary[:240]
        self.status_code = status_code
        self.provider_code = provider_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{category.value}: {self.summary}")


@dataclass(frozen=True, slots=True)
class TushareTable:
    fields: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]
    has_more: bool = False  # 响应信封级完整性标志（008 股东数据分页续取用）


class TushareClient:
    """Protocol-only client; no endpoint-specific fields or mapping live here."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.tushare.pro",
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._api_url = api_url
        self._transport = transport
        self._timeout = timeout

    def call(
        self,
        api_name: str,
        *,
        params: Mapping[str, Any],
        fields: tuple[str, ...],
        allow_empty: bool = False,
    ) -> TushareTable:
        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": dict(params),
            "fields": ",".join(fields),
        }
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(self._api_url, json=payload)
        except httpx.HTTPError as exc:
            raise TushareError(
                TushareErrorCategory.NETWORK,
                "网络连接或超时错误",
            ) from exc

        if response.status_code == 429:
            retry_after: float | None = None
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except ValueError:
                pass
            raise TushareError(
                TushareErrorCategory.RATE_LIMITED,
                "上游短时频率限制",
                status_code=response.status_code,
                retry_after_seconds=retry_after,
            )
        if response.status_code >= 500:
            raise TushareError(
                TushareErrorCategory.UPSTREAM_UNAVAILABLE,
                "上游服务暂时不可用",
                status_code=response.status_code,
            )
        if not 200 <= response.status_code < 300:
            raise TushareError(
                TushareErrorCategory.BAD_REQUEST,
                "上游拒绝请求",
                status_code=response.status_code,
            )

        try:
            envelope = response.json()
            code = envelope["code"]
        except (ValueError, KeyError, TypeError) as exc:
            raise TushareError(
                TushareErrorCategory.INVALID_PAYLOAD, "响应信封不是有效 JSON 结构"
            ) from exc
        if not isinstance(code, int):
            raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "响应 code 类型非法")
        if code != 0:
            message = str(envelope.get("msg") or "")
            category = _classify_business_error(message)
            raise TushareError(category, _safe_business_summary(category), provider_code=code)

        try:
            data = envelope["data"]
            response_fields = data["fields"]
            items = data["items"]
        except (KeyError, TypeError) as exc:
            raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "响应缺少表格字段") from exc
        has_more = bool(data.get("has_more", False))
        if not isinstance(response_fields, list) or not all(
            isinstance(field, str) for field in response_fields
        ):
            raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "fields 结构非法")
        if set(response_fields) != set(fields):
            raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "响应字段与请求不一致")
        if not isinstance(items, list):
            raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "items 结构非法")
        if not items and not allow_empty:
            raise TushareError(TushareErrorCategory.EMPTY_PAYLOAD, "上游返回空数据")

        rows: list[Mapping[str, Any]] = []
        for item in items:
            if not isinstance(item, list) or len(item) != len(response_fields):
                raise TushareError(TushareErrorCategory.INVALID_PAYLOAD, "响应行列数量不匹配")
            rows.append(MappingProxyType(dict(zip(response_fields, item, strict=True))))
        return TushareTable(tuple(response_fields), tuple(rows), has_more=has_more)


def _classify_business_error(message: str) -> TushareErrorCategory:
    normalized = message.lower()
    if any(keyword in normalized for keyword in ("积分", "额度", "配额")):
        return TushareErrorCategory.QUOTA_EXHAUSTED
    if any(keyword in normalized for keyword in ("token", "权限", "认证")):
        return TushareErrorCategory.AUTHENTICATION
    if any(keyword in normalized for keyword in ("频率", "每分钟", "访问太频繁")):
        return TushareErrorCategory.RATE_LIMITED
    if any(keyword in normalized for keyword in ("参数", "api_name", "接口名")):
        return TushareErrorCategory.BAD_REQUEST
    return TushareErrorCategory.UPSTREAM_BUSINESS


def _safe_business_summary(category: TushareErrorCategory) -> str:
    summaries = {
        TushareErrorCategory.QUOTA_EXHAUSTED: "账户调用额度不足",
        TushareErrorCategory.AUTHENTICATION: "凭据无效或权限不足",
        TushareErrorCategory.RATE_LIMITED: "上游短时频率限制",
        TushareErrorCategory.BAD_REQUEST: "上游请求参数错误",
        TushareErrorCategory.UPSTREAM_BUSINESS: "上游业务错误",
    }
    return summaries[category]
