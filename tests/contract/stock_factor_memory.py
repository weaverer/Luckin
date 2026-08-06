"""股票技术面因子 Memory Provider 与 ClickHouse 替身（契约测试与单元测试共用）。

本文件同时充当 ED-006/ED-007 的替代实现证明：Service 契约测试全部基于
Memory 替身运行，与 Tushare Adapter 零耦合；替换 Provider 实现后行为
不变由替身重跑同一验收集证明。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode
from lucking.models.stock_factor import (
    STOCK_FACTOR_FIELDS,
    ProviderStockFactorBatch,
    ProviderStockFactorRecord,
    StockFactorRequest,
)

DEFAULT_STOCK_CODES = ("600000.SH", "000001.SZ", "300750.SZ", "830799.BJ")


def make_record(
    ts_code: str,
    trade_date: date,
    *,
    close: Decimal = Decimal("10.0000"),
    extra: Mapping[str, object] | None = None,
) -> ProviderStockFactorRecord:
    """构造一条因子记录；白名单字段默认全部 None，可按 extra 覆盖。"""
    values: dict[str, Decimal | int | None] = {name: None for name in STOCK_FACTOR_FIELDS}
    values["open"] = Decimal("9.9000")
    values["high"] = Decimal("10.1000")
    values["low"] = Decimal("9.8000")
    values["close_qfq"] = Decimal("9.9500")
    values["close_hfq"] = Decimal("20.5000")
    values["adj_factor"] = Decimal("2.0500")
    values["pe_ttm"] = Decimal("12.5000")
    values["ma_bfq_5"] = Decimal("9.9500")
    values["macd_bfq"] = Decimal("0.1200")
    values["updays"] = 3
    if extra:
        for name, value in extra.items():
            if name not in STOCK_FACTOR_FIELDS:
                raise ValueError(f"未知字段：{name}")
            values[name] = value
    venue = {
        ".SH": VenueCode.SHANGHAI,
        ".SZ": VenueCode.SHENZHEN,
        ".BJ": VenueCode.BEIJING,
    }[ts_code[-3:]]
    return ProviderStockFactorRecord(
        trade_date=trade_date,
        provider_security_id=ts_code,
        venue_code=venue,
        security_code=ts_code.split(".")[0],
        close=close,
        values=values,
    )


class MemoryStockFactorProvider:
    """固定股票集的供应商替身；可挂起股票、注入失败与统计调用次数。"""

    provider_code = "memory"

    def __init__(
        self,
        codes: tuple[str, ...] = DEFAULT_STOCK_CODES,
        suspended: frozenset[str] = frozenset(),
        no_quote_codes: frozenset[str] = frozenset(),
    ) -> None:
        self.codes = codes
        self.suspended = suspended
        self.no_quote_codes = no_quote_codes
        self.call_count = 0
        self.requested_dates: list[date] = []
        self.fail_with: Exception | None = None

    def fetch_stock_factors(
        self, request: StockFactorRequest, *, deadline: float
    ) -> ProviderStockFactorBatch:
        self.call_count += 1
        self.requested_dates.append(request.target_trade_date)
        if self.fail_with is not None:
            raise self.fail_with
        records = tuple(
            make_record(code, request.target_trade_date)
            for code in self.codes
            if code not in self.suspended and code not in self.no_quote_codes
        )
        isolated = tuple(
            ProviderInvalidCandidate(
                category="INVALID_FIELD",
                safe_summary="股票当日无行情（收盘价缺失）",
                provider_security_id=code,
                security_code=code,
            )
            for code in self.codes
            if code in self.no_quote_codes
        )
        received = len(records) + len(isolated)
        return ProviderStockFactorBatch(
            provider_code=self.provider_code,
            target_trade_date=request.target_trade_date,
            records=records,
            evidence=RetrievalEvidence(
                request_count=1,
                completed_request_count=1,
                retry_count=0,
                page_count=1,
                page_limit=10000,
                last_page_count=received,
                received_count=received,
                pagination_enabled=False,
                continuation_exhausted=True,
                repeated_page_detected=False,
            ),
            acquired_at=datetime.now(UTC),
            isolated=isolated,
        )


class MemoryClickHouse:
    """内存 ClickHouse 替身：记录发布批次并可注入失败。"""

    def __init__(self) -> None:
        self.published: list[tuple[date, int]] = []
        self.fail_insert = False

    def publish_batch(
        self,
        trade_date: date,
        records: tuple[object, ...],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        if self.fail_insert:
            raise RuntimeError("ClickHouse 不可达")
        self.published.append((trade_date, len(records)))
        return len(records), 0, 0

    def query_stock_factors(
        self, *args: object, **kwargs: object
    ) -> tuple[dict[str, object], ...]:
        return ()

    def count(self, trade_date: date) -> int:
        return 0
