"""指数技术因子 Memory Provider 与 ClickHouse 替身（契约测试与单元测试共用）。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from lucking.models.index_factor import (
    FACTOR_FIELDS,
    IndexFactorRequest,
    ProviderIndexFactorBatch,
    ProviderIndexFactorRecord,
)
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence

DEFAULT_INDEX_CODES = ("000001.SH", "399001.SZ", "801010.SI", "000300.CSI")


def make_record(
    ts_code: str,
    trade_date: date,
    *,
    close: Decimal = Decimal("3000.0000"),
    open_: Decimal = Decimal("2990.0000"),
) -> ProviderIndexFactorRecord:
    """构造一条因子记录；默认基础行情固定，因子部分填充部分为空。"""
    factors = {name: None for name in FACTOR_FIELDS}
    factors["ma_5"] = Decimal("2995.0000")
    factors["macd"] = Decimal("12.3456")
    factors["rsi_6"] = Decimal("55.5000")
    factors["boll_upper"] = Decimal("3100.0000")
    factors["updays"] = 3
    return ProviderIndexFactorRecord(
        trade_date=trade_date,
        provider_security_id=ts_code,
        open=open_,
        high=Decimal("3010.0000"),
        low=Decimal("2980.0000"),
        close=close,
        pre_close=Decimal("2980.0000"),
        change=Decimal("20.0000"),
        pct_chg=Decimal("0.6700"),
        vol=Decimal("1234567.00"),
        amount=Decimal("456789012.00"),
        **factors,
    )


class MemoryIndexFactorProvider:
    """固定指数集的供应商替身；可挂起指数、注入失败与统计调用次数。"""

    provider_code = "memory"

    def __init__(
        self,
        codes: tuple[str, ...] = DEFAULT_INDEX_CODES,
        suspended: frozenset[str] = frozenset(),
        no_quote_codes: frozenset[str] = frozenset(),
    ) -> None:
        self.codes = codes
        self.suspended = suspended
        self.no_quote_codes = no_quote_codes
        self.call_count = 0
        self.requested_dates: list[date] = []
        self.fail_with: Exception | None = None

    def fetch_index_factors(
        self, request: IndexFactorRequest, *, deadline: float
    ) -> ProviderIndexFactorBatch:
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
                safe_summary="指数当日无行情（收盘价缺失）",
                provider_security_id=code,
                security_code=code,
            )
            for code in self.codes
            if code in self.no_quote_codes
        )
        received = len(records) + len(isolated)
        return ProviderIndexFactorBatch(
            provider_code=self.provider_code,
            target_trade_date=request.target_trade_date,
            records=records,
            evidence=RetrievalEvidence(
                request_count=1,
                completed_request_count=1,
                retry_count=0,
                page_count=1,
                page_limit=8000,
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

    def query(self, *args: object, **kwargs: object) -> tuple[dict[str, object], ...]:
        return ()

    def count(self, trade_date: date) -> int:
        return 0
