"""行情数据 Memory Provider 测试替身与一致性校验套件。

Memory Provider 是契约的参考实现：任何真实 Provider 必须通过同一套
一致性断言（记录数、覆盖证据、字段集合、交易日一致性）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from lucking.models.market_data import RetrievalEvidence, VenueCode
from lucking.ports.adj_factor_provider import (
    AdjFactorRequest,
    ProviderAdjFactor,
    ProviderAdjFactorBatch,
)
from lucking.ports.daily_basic_provider import (
    DailyBasicRequest,
    ProviderDailyBasic,
    ProviderDailyBasicBatch,
)
from lucking.ports.daily_quote_provider import (
    DailyQuoteRequest,
    ProviderDailyQuote,
    ProviderDailyQuoteBatch,
)
from lucking.ports.weekly_monthly_kline_provider import (
    KlineFreq,
    KlineRequest,
    ProviderKlineBatch,
    ProviderWeeklyMonthlyKline,
)

_MARKET_COUNT = 5400  # 全市场单日约 5,400 行


def _security_id(index: int) -> tuple[str, VenueCode, str]:
    if index < 3000:
        return f"{index + 1:06d}.SH", VenueCode.SHANGHAI, f"{index + 1:06d}"
    if index < 5000:
        return f"{index + 1:06d}.SZ", VenueCode.SHENZHEN, f"{index + 1:06d}"
    return f"{index + 1:06d}.BJ", VenueCode.BEIJING, f"{index + 1:06d}"


class MemoryDailyQuoteProvider:
    provider_code = "memory"

    def __init__(self, *, suspended: frozenset[str] = frozenset()) -> None:
        self._suspended = suspended

    def fetch_daily_quotes(
        self,
        request: DailyQuoteRequest,
        *,
        deadline: float,
    ) -> ProviderDailyQuoteBatch:
        records: list[ProviderDailyQuote] = []
        for index in range(_MARKET_COUNT):
            provider_id, venue, code = _security_id(index)
            if provider_id in self._suspended:
                continue
            records.append(
                ProviderDailyQuote(
                    trade_date=request.target_trade_date,
                    provider_security_id=provider_id,
                    venue_code=venue,
                    security_code=code,
                    open=Decimal("10.0000"),
                    high=Decimal("11.0000"),
                    low=Decimal("9.5000"),
                    close=Decimal("10.5000"),
                    pre_close=Decimal("10.0000"),
                    change=Decimal("0.5000"),
                    pct_chg=Decimal("5.000"),
                    vol=Decimal("123456.00"),
                    amount=Decimal("1234567.00"),
                )
            )
        return ProviderDailyQuoteBatch(
            self.provider_code,
            request.target_trade_date,
            records,
            _evidence(len(records)),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


class MemoryAdjFactorProvider:
    provider_code = "memory"

    def fetch_adj_factors(
        self,
        request: AdjFactorRequest,
        *,
        deadline: float,
    ) -> ProviderAdjFactorBatch:
        records = tuple(
            ProviderAdjFactor(
                trade_date=request.target_trade_date,
                provider_security_id=provider_id,
                venue_code=venue,
                security_code=code,
                adj_factor=Decimal("1.234567"),
            )
            for index in range(_MARKET_COUNT)
            for provider_id, venue, code in (_security_id(index),)
        )
        return ProviderAdjFactorBatch(
            self.provider_code,
            request.target_trade_date,
            records,
            _evidence(len(records)),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


class MemoryDailyBasicProvider:
    provider_code = "memory"

    def __init__(self, *, loss_making: frozenset[str] = frozenset()) -> None:
        self._loss_making = loss_making

    def fetch_daily_basics(
        self,
        request: DailyBasicRequest,
        *,
        deadline: float,
    ) -> ProviderDailyBasicBatch:
        records = tuple(
            ProviderDailyBasic(
                trade_date=request.target_trade_date,
                provider_security_id=provider_id,
                venue_code=venue,
                security_code=code,
                pe=None if provider_id in self._loss_making else Decimal("15.2500"),
                pe_ttm=None if provider_id in self._loss_making else Decimal("14.1000"),
                pb=None if provider_id in self._loss_making else Decimal("1.5000"),
                ps=Decimal("1.2000"),
                ps_ttm=Decimal("1.1000"),
                dv_ratio=Decimal("2.5000"),
                dv_ttm=Decimal("2.6000"),
                total_share=Decimal("100000.0000"),
                float_share=Decimal("80000.0000"),
                free_share=Decimal("70000.0000"),
                total_mv=Decimal("1000000.0000"),
                circ_mv=Decimal("800000.0000"),
                turnover_rate=Decimal("1.5000"),
                turnover_rate_f=Decimal("1.7000"),
                volume_ratio=Decimal("1.2000"),
                limit_status=0,
            )
            for index in range(_MARKET_COUNT)
            for provider_id, venue, code in (_security_id(index),)
        )
        return ProviderDailyBasicBatch(
            self.provider_code,
            request.target_trade_date,
            records,
            _evidence(len(records)),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


class MemoryWeeklyMonthlyKlineProvider:
    provider_code = "memory"

    def fetch_kline(
        self,
        request: KlineRequest,
        *,
        deadline: float,
    ) -> ProviderKlineBatch:
        period_trade_date = _period_last_trade_date(request.freq, request.target_trade_date)
        records = tuple(
            ProviderWeeklyMonthlyKline(
                freq=request.freq,
                trade_date=period_trade_date,
                end_date=None,
                provider_security_id=provider_id,
                venue_code=venue,
                security_code=code,
                open=Decimal("10.0000"),
                high=Decimal("11.0000"),
                low=Decimal("9.5000"),
                close=Decimal("10.5000"),
                vol=Decimal("123456.00"),
                amount=Decimal("1234567.00"),
                change=Decimal("0.5000"),
                pct_chg=Decimal("5.000"),
            )
            for index in range(_MARKET_COUNT)
            for provider_id, venue, code in (_security_id(index),)
        )
        return ProviderKlineBatch(
            self.provider_code,
            request.freq,
            request.target_trade_date,
            records,
            _evidence(len(records)),
            datetime(2026, 7, 1, tzinfo=UTC),
        )


def _period_last_trade_date(freq: KlineFreq, request_date: date) -> date:
    """周期最后交易日参考实现：不晚于请求交易日且同周期内稳定。

    WEEK：请求日所在周之前最近一个周五；MONTH：请求日为月末时取当月，
    否则取上一月末。满足"不晚于请求交易日"并保证同周期多日请求返回相同日期。
    """
    if freq is KlineFreq.WEEK:
        candidate = request_date
        while candidate.weekday() != 4:  # 周五
            candidate -= timedelta(days=1)
        return candidate
    month_end = (request_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
        days=1
    )
    if request_date == month_end:
        return month_end
    return request_date.replace(day=1) - timedelta(days=1)  # 上一月末


def _evidence(received: int) -> RetrievalEvidence:
    return RetrievalEvidence(
        request_count=1,
        completed_request_count=1,
        retry_count=0,
        page_count=1,
        page_limit=6000,
        last_page_count=received,
        received_count=received,
        pagination_enabled=False,
        continuation_exhausted=True,
    )


def assert_provider_batch_consistent(batch: object) -> None:
    """Memory 一致性套件：所有 Provider 批次必须满足的公共断言。"""
    assert batch.evidence.completed_request_count == batch.evidence.request_count
    assert batch.evidence.continuation_exhausted
    assert not batch.evidence.repeated_page_detected
    assert 0 <= batch.evidence.last_page_count < batch.evidence.page_limit
    assert batch.evidence.received_count == len(batch.records)
