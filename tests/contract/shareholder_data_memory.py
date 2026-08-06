"""股东数据 Memory Provider 与 ClickHouse 替身（契约测试与单元测试共用）。

本文件同时充当 ED-006/ED-007 的替代实现证明：Service 契约测试全部基于
Memory 替身运行，与 Tushare Adapter 零耦合；替换 Provider 实现后行为
不变由替身重跑同一验收集证明。

MemoryClickHouse 复刻真实仓储的发布语义（data-model.md §4）：按业务身份
同键比较，值不同且新公告（ann_date 更大）→ updated，值不同且非新公告 →
RECORD_CONFLICT，值相同 → unchanged；水位按接口/kind 分别记录
（两 top10 接口同表但水位独立，shareholder-data-service.md §4-3）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode
from lucking.models.shareholder_data import (
    ProviderShareholderBatch,
    ProviderShareholderCountBatch,
    ProviderShareholderCountRecord,
    ProviderShareholderRecord,
    ShareholderDataRequest,
)

DEFAULT_STOCK_CODES = ("600000.SH", "000001.SZ", "300750.SZ", "830799.BJ")
_VENUES = {
    ".SH": VenueCode.SHANGHAI,
    ".SZ": VenueCode.SHENZHEN,
    ".BJ": VenueCode.BEIJING,
}


def _split(ts_code: str) -> tuple[str, VenueCode, str]:
    venue = _VENUES[ts_code[-3:]]
    return ts_code, venue, ts_code.split(".")[0]


def make_holding_record(
    ts_code: str,
    ann_date: date,
    end_date: date,
    *,
    holder_name: str = "测试股东",
    hold_amount: Decimal | None = Decimal("1000000.00"),
    hold_ratio: Decimal | None = Decimal("1.5000"),
    hold_float_ratio: Decimal | None = Decimal("1.5000"),
    hold_change: Decimal | None = Decimal("0.0000"),
    holder_type: str | None = "一般企业",
    extra: dict[str, object] | None = None,
) -> ProviderShareholderRecord:
    if extra:
        for name, value in extra.items():
            if name == "holder_name":
                holder_name = str(value)
            elif name == "hold_amount":
                hold_amount = value  # type: ignore[assignment]
            elif name == "hold_ratio":
                hold_ratio = value  # type: ignore[assignment]
            elif name == "hold_float_ratio":
                hold_float_ratio = value  # type: ignore[assignment]
            elif name == "hold_change":
                hold_change = value  # type: ignore[assignment]
            elif name == "holder_type":
                holder_type = str(value) if value is not None else None
            else:
                raise ValueError(f"未知覆盖字段：{name}")
    provider_id, venue, security_code = _split(ts_code)
    return ProviderShareholderRecord(
        provider_security_id=provider_id,
        venue_code=venue,
        security_code=security_code,
        ann_date=ann_date,
        end_date=end_date,
        holder_name=holder_name,
        hold_amount=hold_amount,
        hold_ratio=hold_ratio,
        hold_float_ratio=hold_float_ratio,
        hold_change=hold_change,
        holder_type=holder_type,
    )


def make_count_record(
    ts_code: str,
    ann_date: date,
    end_date: date,
    *,
    holder_num: int | None = 98777,
    extra: dict[str, object] | None = None,
) -> ProviderShareholderCountRecord:
    if extra:
        if "holder_num" in extra:
            holder_num = extra["holder_num"]  # type: ignore[assignment]
        else:
            raise ValueError(f"未知覆盖字段：{sorted(extra)}")
    provider_id, venue, security_code = _split(ts_code)
    return ProviderShareholderCountRecord(
        provider_security_id=provider_id,
        venue_code=venue,
        security_code=security_code,
        ann_date=ann_date,
        end_date=end_date,
        holder_num=holder_num,
    )


class MemoryShareholderDataProvider:
    """固定股票集的供应商替身；可挂起股票、注入失败与统计调用次数。"""

    provider_code = "memory"

    def __init__(
        self,
        codes: tuple[str, ...] = DEFAULT_STOCK_CODES,
        suspended: frozenset[str] = frozenset(),
        empty_dates: frozenset[date] = frozenset(),
        failures: dict[str, Exception] | None = None,
        bad_continuation: bool = False,
        value_overrides: dict[date, dict[str, object]] | None = None,
    ) -> None:
        self.codes = codes
        self.suspended = suspended
        self.empty_dates = empty_dates
        self.fail_with: Exception | None = None
        self.failures: dict[str, Exception] = failures or {}  # 按方法名注入（故障隔离测试）
        self.bad_continuation = bad_continuation  # 模拟分页未收敛（完整性门禁测试）
        self.value_overrides = value_overrides or {}  # 按公告日覆盖字段值（修订语义测试）
        self.call_counts: dict[str, int] = {
            "TOP10": 0,
            "TOP10_FLOAT": 0,
            "HOLDER_COUNT": 0,
        }
        self.requested_dates: dict[str, list[date]] = {
            "TOP10": [],
            "TOP10_FLOAT": [],
            "HOLDER_COUNT": [],
        }

    def _maybe_fail(self, method: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        failure = self.failures.get(method)
        if failure is not None:
            raise failure

    def fetch_top10_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch:
        return self._fetch("TOP10", request, deadline, make_holding_record)

    def fetch_top10_float_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch:
        return self._fetch("TOP10_FLOAT", request, deadline, make_holding_record)

    def fetch_holder_count(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderCountBatch:
        return self._fetch("HOLDER_COUNT", request, deadline, make_count_record)

    def _fetch(
        self,
        kind: str,
        request: ShareholderDataRequest,
        deadline: float,
        builder: Any,
    ) -> Any:
        self.call_counts[kind] += 1
        self.requested_dates[kind].append(request.date)
        self._maybe_fail(kind)
        if request.date in self.empty_dates:
            records = ()
            isolated = ()
            received = 0
        else:
            # 测试用披露期：请求月 28 日（同月内唯一，便于身份键构造）
            end_date = request.date.replace(day=28)
            extra = self.value_overrides.get(request.date)
            records = tuple(
                builder(code, request.date, end_date, extra=extra)
                for code in self.codes
                if code not in self.suspended
            )
            isolated = tuple(
                ProviderInvalidCandidate(
                    category="INVALID_FIELD",
                    safe_summary="股东记录无效（测试注入）",
                    provider_security_id=code,
                    security_code=code,
                )
                for code in self.codes
                if code in self.suspended
            )
            received = len(records) + len(isolated)
        evidence = RetrievalEvidence(
            request_count=1,
            completed_request_count=1,
            retry_count=0,
            page_count=1,
            page_limit=6000,
            last_page_count=received,
            received_count=received,
            pagination_enabled=True,
            continuation_exhausted=not self.bad_continuation,
            repeated_page_detected=False,
        )
        if kind == "HOLDER_COUNT":
            return ProviderShareholderCountBatch(
                provider_code=self.provider_code,
                request_date=request.date,
                records=tuple(records),
                evidence=evidence,
                acquired_at=datetime.now(UTC),
                isolated=isolated,
            )
        return ProviderShareholderBatch(
            provider_code=self.provider_code,
            request_date=request.date,
            records=tuple(records),
            evidence=evidence,
            acquired_at=datetime.now(UTC),
            isolated=isolated,
        )


class MemoryClickHouse:
    """内存 ClickHouse 替身：复刻真实仓储发布语义并记录水位。"""

    def __init__(self) -> None:
        self.holdings: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.counts: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_insert = False
        self.published: list[str] = []

    # 水位（按接口/kind，shareholder-data-service.md §4-3）
    def top10_holders_watermark(self) -> date | None:
        return self._watermark(self.holdings, ("TOP10",))

    def top10_float_holders_watermark(self) -> date | None:
        return self._watermark(self.holdings, ("TOP10_FLOAT",))

    def holder_count_watermark(self) -> date | None:
        return self._watermark(self.counts, None)

    def _watermark(
        self, store: dict[Any, dict[str, Any]], kinds: tuple[str, ...] | None
    ) -> date | None:
        values: list[date] = []
        for key, row in store.items():
            if kinds is not None and key[2] not in kinds:
                continue
            ann = row.get("ann_date")
            if ann is not None:
                values.append(ann if isinstance(ann, date) else date.fromisoformat(str(ann)[:10]))
        return max(values) if values else None

    def publish_holdings(
        self, records: tuple[Any, ...], updated_at: datetime
    ) -> tuple[int, int, int]:
        if self.fail_insert:
            raise RuntimeError("ClickHouse 不可达")
        self.published.append("holdings")
        added = updated = unchanged = 0
        for record in records:
            key = (
                record.end_date.isoformat(),
                record.stock_id,
                record.holder_kind,
                record.holder_name,
            )
            previous = self.holdings.get(key)
            if previous is None:
                added += 1
            else:
                classification = _classify(record, previous, ("hold_amount", "hold_ratio",
                                                              "hold_float_ratio", "hold_change",
                                                              "holder_type"))
                if classification == "updated":
                    updated += 1
                else:
                    unchanged += 1
            self.holdings[key] = {
                "ann_date": record.ann_date,
                "stock_code": record.stock_code,
                "hold_amount": record.hold_amount,
                "hold_ratio": record.hold_ratio,
                "hold_float_ratio": record.hold_float_ratio,
                "hold_change": record.hold_change,
                "holder_type": record.holder_type,
                "updated_at": updated_at,
            }
        return added, updated, unchanged

    def publish_counts(
        self, records: tuple[Any, ...], updated_at: datetime
    ) -> tuple[int, int, int]:
        if self.fail_insert:
            raise RuntimeError("ClickHouse 不可达")
        self.published.append("counts")
        added = updated = unchanged = 0
        for record in records:
            key = (record.end_date.isoformat(), record.stock_id)
            previous = self.counts.get(key)
            if previous is None:
                added += 1
            else:
                classification = _classify(record, previous, ("holder_num",))
                if classification == "updated":
                    updated += 1
                else:
                    unchanged += 1
            self.counts[key] = {
                "ann_date": record.ann_date,
                "stock_code": record.stock_code,
                "holder_num": record.holder_num,
                "updated_at": updated_at,
            }
        return added, updated, unchanged

    def query_shareholder_holdings(
        self, *args: object, **kwargs: object
    ) -> tuple[dict[str, object], ...]:
        return ()

    def query_shareholder_count(
        self, *args: object, **kwargs: object
    ) -> tuple[dict[str, object], ...]:
        return ()


def _classify(record: Any, previous: dict[str, Any], data_columns: tuple[str, ...]) -> str:
    incoming_ann = record.ann_date
    existing_ann = previous.get("ann_date")
    if isinstance(existing_ann, str):
        existing_ann = date.fromisoformat(existing_ann[:10])
    differs = any(getattr(record, column) != previous.get(column) for column in data_columns)
    if not differs:
        return "unchanged"
    if incoming_ann is not None and existing_ann is not None and incoming_ann > existing_ann:
        return "updated"
    from lucking.repositories.market_data import MarketDataValidationError

    raise MarketDataValidationError("RECORD_CONFLICT", "同一业务身份存在非新公告的字段冲突")
