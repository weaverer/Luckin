"""股票技术面因子规范化模型（stk_factor_pro 专业版）。

设计要点（research 决策 7）：
- 规范字段名 = 来源字段名原样保留（含 ``_bfq/_qfq/_hfq`` 复权变体后缀）；
  复权变体是语义必要组成部分，消费方无法自行还原来源计算的复权指标。
- 白名单 ``STOCK_FACTOR_FIELDS`` 为唯一事实来源（ED-005）；本模块按来源文档
  分组清单程序化展开，部署账户实测（research 待验证项 2 / tasks T008）后校准。
- 字段分级（spec FR-010/ED-009）：可修订字段 = 字段名含 ``_qfq``/``_hfq``
  后缀者 + ``adj_factor``（随后续除权除息重算，重复同步按来源最新值更新，
  不视为冲突）；其余为稳定字段（同键值变化即 RECORD_CONFLICT）。
- 数据列以 ``values`` 映射承载（160+ 列宽表，逐列显式枚举不具可维护性），
  键全集即 ``STOCK_FACTOR_FIELDS``；``close`` 为行情锚点（唯一必填，
  spec FR-014 语义），独立显式字段。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode

# 行情价格原值（open/high/low 可空；close 为独立锚点字段，不在 values 中）。
_PRICE_BASE_FIELDS = ("open", "high", "low")
# 行情价格复权变体：实测（2026-08-04，trade_date=20260803 全量 5529 行）
# 返回 open_qfq/open_hfq 形态，**无 _bfq 价格变体**（原值即不复权）。
_PRICE_VARIANTS = ("qfq", "hfq")
PRICE_VARIANT_FIELDS: tuple[str, ...] = tuple(
    f"{base}_{variant}"
    for base in ("open", "high", "low", "close")
    for variant in _PRICE_VARIANTS
)
# 其余行情字段（close 之外）。
QUOTE_FIELDS: tuple[str, ...] = (
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "adj_factor",
)
# 估值字段。
VALUATION_FIELDS: tuple[str, ...] = (
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
)
# 技术指标基名（来源文档分组清单；实测 2026-08-04 确认各含 _bfq/_qfq/_hfq
# 三变体，全部返回）。命名规则沿用来源既定形态（006 实测验证，同源命名）：
# ma/ema/rsi 周期组为前缀式（ma_bfq_5），其余为后缀式（kdj_bfq）。
_PREFIX_PERIOD_GROUPS: frozenset[str] = frozenset({"ma", "ema", "rsi"})
_INDICATOR_BASES: tuple[str, ...] = (
    "asi", "asit", "atr", "bbi",
    "bias1", "bias2", "bias3",
    "boll_lower", "boll_mid", "boll_upper",
    "brar_ar", "brar_br",
    "cci", "cr",
    "dfma_dif", "dfma_difma",
    "dmi_pdi", "dmi_mdi", "dmi_adx", "dmi_adxr",
    "dpo", "madpo",
    "ema_5", "ema_10", "ema_20", "ema_30", "ema_60", "ema_90", "ema_250",
    "emv", "maemv",
    "expma_12", "expma_50",
    "kdj", "kdj_k", "kdj_d",
    "ktn_upper", "ktn_mid", "ktn_down",
    "ma_5", "ma_10", "ma_20", "ma_30", "ma_60", "ma_90", "ma_250",
    "macd", "macd_dea", "macd_dif",
    "mass", "ma_mass",
    "mfi",
    "mtm", "mtmma",
    "obv",
    "psy", "psyma",
    "roc", "maroc",
    "rsi_6", "rsi_12", "rsi_24",
    "taq_up", "taq_mid", "taq_down",
    "trix", "trma",
    "vr",
    "wr", "wr1",
    "xsii_td1", "xsii_td2", "xsii_td3", "xsii_td4",
)
_VARIANTS = ("bfq", "qfq", "hfq")


def _indicator_field(base: str, variant: str) -> str:
    """基名 + 变体 → 来源字段名（ma/ema/rsi 前缀式周期，其余后缀式变体）。

    仅当周期段为**纯数字**时才走前缀式（ma_bfq_5）；`ma_mass` 的 period 为
    "mass"，必须走后缀式（ma_mass_bfq）——实测 2026-08-05 校准确认。
    """
    family, _, period = base.partition("_")
    if family in _PREFIX_PERIOD_GROUPS and period.isdigit():
        return f"{family}_{variant}_{period}"
    return f"{base}_{variant}"


INDICATOR_FIELDS: tuple[str, ...] = tuple(
    _indicator_field(base, variant) for base in _INDICATOR_BASES for variant in _VARIANTS
)
# 连涨/连跌/区间高低天数（整数列，ClickHouse UInt16）。
DAY_COUNT_FIELDS: tuple[str, ...] = ("updays", "downdays", "lowdays", "topdays")

# 全部数据字段白名单（values 映射键全集；close 锚点独立）。
STOCK_FACTOR_FIELDS: tuple[str, ...] = (
    _PRICE_BASE_FIELDS
    + PRICE_VARIANT_FIELDS
    + QUOTE_FIELDS
    + VALUATION_FIELDS
    + INDICATOR_FIELDS
    + DAY_COUNT_FIELDS
)

# 可修订字段：复权变体（_qfq/_hfq 后缀，实测无 _bfq 价格变体）与
# 累计复权因子 adj_factor。
REVISION_ALLOWED_FIELDS: frozenset[str] = frozenset(
    field
    for field in STOCK_FACTOR_FIELDS
    if field.endswith("_qfq") or field.endswith("_hfq")
) | frozenset({"adj_factor"})

DAY_COUNT_SET: frozenset[str] = frozenset(DAY_COUNT_FIELDS)


@dataclass(frozen=True, slots=True)
class StockFactorRequest:
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderStockFactorRecord:
    """供应商无关的股票技术面因子记录。

    ``provider_security_id`` 与 ``venue_code/security_code`` 仅用于身份解析
    （003 主数据）；``close`` 为行情锚点（唯一必填，缺失即整条隔离）；
    其余全部字段以 ``values`` 承载（键全集 = STOCK_FACTOR_FIELDS，
    可空；空表示来源未返回该值，不等于业务无效）。
    """

    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    close: Decimal
    values: Mapping[str, Decimal | int | None]


@dataclass(frozen=True, slots=True)
class StockFactor:
    """规范化股票技术面因子，身份已解析为稳定 stock_id。"""

    trade_date: date
    stock_id: str
    stock_code: str
    close: Decimal
    values: Mapping[str, Decimal | int | None]


@dataclass(frozen=True, slots=True)
class ProviderStockFactorBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderStockFactorRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()
