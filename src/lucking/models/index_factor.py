"""指数技术因子规范化模型与指数身份持久化模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lucking.db import Base
from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence

# 78 个技术因子规范字段（来源字段去掉 _bfq 后缀）；缺失以 None 保存。
FACTOR_FIELDS: tuple[str, ...] = (
    "ma_5", "ma_10", "ma_20", "ma_30", "ma_60", "ma_90", "ma_250",
    "ema_5", "ema_10", "ema_20", "ema_30", "ema_60", "ema_90", "ema_250",
    "expma_12", "expma_50", "bbi",
    "macd", "macd_dea", "macd_dif",
    "kdj", "kdj_k", "kdj_d",
    "rsi_6", "rsi_12", "rsi_24",
    "cci", "wr", "wr1",
    "bias1", "bias2", "bias3",
    "psy", "psyma", "roc", "maroc", "mfi", "mtm", "mtmma",
    "boll_lower", "boll_mid", "boll_upper",
    "ktn_down", "ktn_mid", "ktn_upper",
    "taq_up", "taq_mid", "taq_down",
    "xsii_td1", "xsii_td2", "xsii_td3", "xsii_td4",
    "dmi_pdi", "dmi_mdi", "dmi_adx", "dmi_adxr",
    "obv", "vr", "emv", "maemv", "cr", "brar_ar", "brar_br",
    "dpo", "madpo", "dfma_dif", "dfma_difma",
    "asi", "asit", "atr", "mass", "ma_mass", "trix", "trma",
    "updays", "downdays", "topdays", "lowdays",
)

_DAY_COUNT_FIELDS = frozenset({"updays", "downdays", "topdays", "lowdays"})


def provider_factor_name(canonical: str) -> str:
    """规范因子名 → 来源字段名。

    来源命名不规则：ma/ema/rsi 的周期组为前缀式（ma_bfq_5），其余为后缀式
    （expma_12_bfq、boll_lower_bfq）；四个天数因子无 _bfq 后缀。
    """
    if canonical in _DAY_COUNT_FIELDS:
        return canonical
    match = re.fullmatch(r"(ma|ema|rsi)_(\d+)", canonical)
    if match:
        return f"{match.group(1)}_bfq_{match.group(2)}"
    return f"{canonical}_bfq"


# 供应商返回的原始字段名（白名单；用于 Adapter 严格校验与规范名映射）。
PROVIDER_FACTOR_FIELDS: tuple[str, ...] = tuple(
    provider_factor_name(name) for name in FACTOR_FIELDS
)
PROVIDER_BASE_FIELDS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_change",
    "vol",
    "amount",
)
PROVIDER_INDEX_FACTOR_FIELDS: tuple[str, ...] = (
    PROVIDER_BASE_FIELDS + PROVIDER_FACTOR_FIELDS
)


@dataclass(frozen=True, slots=True)
class IndexFactorRequest:
    target_trade_date: date


@dataclass(frozen=True, slots=True)
class ProviderIndexFactorRecord:
    """供应商无关的指数技术因子记录；provider_security_id 仅用于身份解析。

    基础行情允许部分缺失（实测 2026-08-02：439/3146 行仅 pre_close 为空，
    H 系列等指数 open 恒为空）；close 为行情锚点，缺失即整条隔离。
    """

    trade_date: date
    provider_security_id: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    pre_close: Decimal | None
    change: Decimal | None
    pct_chg: Decimal | None
    vol: Decimal | None
    amount: Decimal | None
    ma_5: Decimal | None
    ma_10: Decimal | None
    ma_20: Decimal | None
    ma_30: Decimal | None
    ma_60: Decimal | None
    ma_90: Decimal | None
    ma_250: Decimal | None
    ema_5: Decimal | None
    ema_10: Decimal | None
    ema_20: Decimal | None
    ema_30: Decimal | None
    ema_60: Decimal | None
    ema_90: Decimal | None
    ema_250: Decimal | None
    expma_12: Decimal | None
    expma_50: Decimal | None
    bbi: Decimal | None
    macd: Decimal | None
    macd_dea: Decimal | None
    macd_dif: Decimal | None
    kdj: Decimal | None
    kdj_k: Decimal | None
    kdj_d: Decimal | None
    rsi_6: Decimal | None
    rsi_12: Decimal | None
    rsi_24: Decimal | None
    cci: Decimal | None
    wr: Decimal | None
    wr1: Decimal | None
    bias1: Decimal | None
    bias2: Decimal | None
    bias3: Decimal | None
    psy: Decimal | None
    psyma: Decimal | None
    roc: Decimal | None
    maroc: Decimal | None
    mfi: Decimal | None
    mtm: Decimal | None
    mtmma: Decimal | None
    boll_lower: Decimal | None
    boll_mid: Decimal | None
    boll_upper: Decimal | None
    ktn_down: Decimal | None
    ktn_mid: Decimal | None
    ktn_upper: Decimal | None
    taq_up: Decimal | None
    taq_mid: Decimal | None
    taq_down: Decimal | None
    xsii_td1: Decimal | None
    xsii_td2: Decimal | None
    xsii_td3: Decimal | None
    xsii_td4: Decimal | None
    dmi_pdi: Decimal | None
    dmi_mdi: Decimal | None
    dmi_adx: Decimal | None
    dmi_adxr: Decimal | None
    obv: Decimal | None
    vr: Decimal | None
    emv: Decimal | None
    maemv: Decimal | None
    cr: Decimal | None
    brar_ar: Decimal | None
    brar_br: Decimal | None
    dpo: Decimal | None
    madpo: Decimal | None
    dfma_dif: Decimal | None
    dfma_difma: Decimal | None
    asi: Decimal | None
    asit: Decimal | None
    atr: Decimal | None
    mass: Decimal | None
    ma_mass: Decimal | None
    trix: Decimal | None
    trma: Decimal | None
    updays: int | None
    downdays: int | None
    topdays: int | None
    lowdays: int | None


@dataclass(frozen=True, slots=True)
class IndexFactor:
    """规范化指数技术因子，身份已解析为稳定 index_id；基础行情可部分缺失。"""

    trade_date: date
    index_id: str
    index_code: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    pre_close: Decimal | None
    change: Decimal | None
    pct_chg: Decimal | None
    vol: Decimal | None
    amount: Decimal | None
    ma_5: Decimal | None
    ma_10: Decimal | None
    ma_20: Decimal | None
    ma_30: Decimal | None
    ma_60: Decimal | None
    ma_90: Decimal | None
    ma_250: Decimal | None
    ema_5: Decimal | None
    ema_10: Decimal | None
    ema_20: Decimal | None
    ema_30: Decimal | None
    ema_60: Decimal | None
    ema_90: Decimal | None
    ema_250: Decimal | None
    expma_12: Decimal | None
    expma_50: Decimal | None
    bbi: Decimal | None
    macd: Decimal | None
    macd_dea: Decimal | None
    macd_dif: Decimal | None
    kdj: Decimal | None
    kdj_k: Decimal | None
    kdj_d: Decimal | None
    rsi_6: Decimal | None
    rsi_12: Decimal | None
    rsi_24: Decimal | None
    cci: Decimal | None
    wr: Decimal | None
    wr1: Decimal | None
    bias1: Decimal | None
    bias2: Decimal | None
    bias3: Decimal | None
    psy: Decimal | None
    psyma: Decimal | None
    roc: Decimal | None
    maroc: Decimal | None
    mfi: Decimal | None
    mtm: Decimal | None
    mtmma: Decimal | None
    boll_lower: Decimal | None
    boll_mid: Decimal | None
    boll_upper: Decimal | None
    ktn_down: Decimal | None
    ktn_mid: Decimal | None
    ktn_upper: Decimal | None
    taq_up: Decimal | None
    taq_mid: Decimal | None
    taq_down: Decimal | None
    xsii_td1: Decimal | None
    xsii_td2: Decimal | None
    xsii_td3: Decimal | None
    xsii_td4: Decimal | None
    dmi_pdi: Decimal | None
    dmi_mdi: Decimal | None
    dmi_adx: Decimal | None
    dmi_adxr: Decimal | None
    obv: Decimal | None
    vr: Decimal | None
    emv: Decimal | None
    maemv: Decimal | None
    cr: Decimal | None
    brar_ar: Decimal | None
    brar_br: Decimal | None
    dpo: Decimal | None
    madpo: Decimal | None
    dfma_dif: Decimal | None
    dfma_difma: Decimal | None
    asi: Decimal | None
    asit: Decimal | None
    atr: Decimal | None
    mass: Decimal | None
    ma_mass: Decimal | None
    trix: Decimal | None
    trma: Decimal | None
    updays: int | None
    downdays: int | None
    topdays: int | None
    lowdays: int | None


@dataclass(frozen=True, slots=True)
class ProviderIndexFactorBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderIndexFactorRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


def _id() -> Mapped[int]:
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="主键ID",
    )


def _created() -> Mapped[datetime]:
    return mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )


class IndexCurrent(Base):
    __tablename__ = "index_current"
    __table_args__ = (
        UniqueConstraint("index_id", name="uq_index_current_index_id"),
        UniqueConstraint("index_code", name="uq_index_current_index_code"),
        CheckConstraint(
            "index_code LIKE '%.SH' OR index_code LIKE '%.SZ' "
            "OR index_code LIKE '%.CSI' OR index_code LIKE '%.SI' "
            "OR index_code LIKE '%.CI' OR index_code LIKE '%.NH' "
            "OR index_code LIKE '%.BJ' OR index_code LIKE '%.CNI'",
            name="ck_index_current_code_suffix",
        ),
        {"comment": "指数主数据（大盘指数、申万行业指数、中信指数）"},
    )

    id: Mapped[int] = _id()
    index_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="规范指数标识（UUID，应用生成）"
    )
    index_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="规范指数代码（来源 ts_code，含 .SH/.SZ/.CSI/.SI 后缀）",
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IndexProviderMapping(Base):
    __tablename__ = "index_provider_mapping"
    __table_args__ = (
        UniqueConstraint(
            "provider_code", "provider_security_id", name="uq_index_provider_mapping"
        ),
        {"comment": "指数来源标识映射（一个来源标识只映射一个规范指数标识）"},
    )

    id: Mapped[int] = _id()
    provider_code: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="数据来源代码（如 tushare）"
    )
    provider_security_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="来源指数标识（ts_code）"
    )
    index_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("index_current.index_id"),
        nullable=False,
        comment="规范指数标识（指向 index_current.index_id）",
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
