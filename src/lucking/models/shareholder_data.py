"""股东数据规范化模型（top10_holders / top10_floatholders / stk_holdernumber）。

设计要点（research 决策 7）：
- 规范字段名 = 来源字段名原样保留；白名单 ``TOP10_HOLDER_FIELDS`` 与
  ``HOLDER_COUNT_FIELDS`` 为唯一事实来源（ED-006），2026-08-05 部署账户
  实测与文档逐名一致（research 待验证项 2，`scripts/probe_shareholder_api5.py`）。
- 业务身份（spec FR-007）：持仓 = (end_date, stock_id, holder_kind,
  holder_name)；股东人数 = (end_date, stock_id)。
- 修订语义（spec FR-010/ED-010）：同一身份出现**新公告**（更大 ann_date）
  时按最新公告值更新（updated_count，不视为冲突）；非新公告的值变化即
  RECORD_CONFLICT 整批失败。
- 三个接口各对应一条独立同步链路（3 Flow 拆分，用户显式要求）：
  审计 data_kind 按接口取值（TOP10_HOLDERS/TOP10_FLOAT_HOLDERS/
  HOLDER_COUNT，与 005 每接口一取值模式一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from lucking.models.market_data import ProviderInvalidCandidate, RetrievalEvidence, VenueCode


class HolderKind(StrEnum):
    TOP10 = "TOP10"
    TOP10_FLOAT = "TOP10_FLOAT"


# 前十大股东与前十大流通股东（两接口字段结构一致，白名单共用；
# ts_code 仅用于身份解析，不进入数据列）。
TOP10_HOLDER_FIELDS: tuple[str, ...] = (
    "ann_date",
    "end_date",
    "holder_name",
    "hold_amount",
    "hold_ratio",
    "hold_float_ratio",
    "hold_change",
    "holder_type",
)
# 股东人数（独立简结构）。
HOLDER_COUNT_FIELDS: tuple[str, ...] = ("ann_date", "end_date", "holder_num")

# 来源返回字段全集（含身份列 ts_code；显式 fields 请求与响应逐名一致，
# 2026-08-05 实测确认）。
PROVIDER_TOP10_HOLDER_FIELDS: tuple[str, ...] = ("ts_code",) + TOP10_HOLDER_FIELDS
PROVIDER_HOLDER_COUNT_FIELDS: tuple[str, ...] = ("ts_code",) + HOLDER_COUNT_FIELDS


@dataclass(frozen=True, slots=True)
class ShareholderDataRequest:
    """按公告日获取全市场股东数据的请求（不传 ts_code，实测验证可行）。"""

    date: date                      # 公告日期（单日）
    holder_kind: str                # HolderKind 或 "HOLDER_COUNT"（区分三接口）


@dataclass(frozen=True, slots=True)
class ProviderShareholderRecord:
    """供应商无关的前十大股东/流通股东持仓记录。"""

    provider_security_id: str       # 供应商股票标识（ts_code，仅身份解析用）
    venue_code: VenueCode           # 规范交易场所（身份解析用）
    security_code: str              # 规范证券代码（身份解析用）
    ann_date: date                  # 公告日期（修订锚点）
    end_date: date                  # 披露期（报告期，业务身份组成部分）
    holder_name: str                # 股东名称（业务身份组成部分）
    hold_amount: Decimal | None     # 持有数量（股）
    hold_ratio: Decimal | None      # 占总股本比例（%）
    hold_float_ratio: Decimal | None  # 占流通股本比例（%）
    hold_change: Decimal | None     # 持股变动（股，可为负）
    holder_type: str | None         # 股东类型


@dataclass(frozen=True, slots=True)
class ProviderShareholderCountRecord:
    """供应商无关的股东人数记录。"""

    provider_security_id: str       # 供应商股票标识（ts_code，仅身份解析用）
    venue_code: VenueCode           # 规范交易场所（身份解析用）
    security_code: str              # 规范证券代码（身份解析用）
    ann_date: date                  # 公告日期（修订锚点）
    end_date: date                  # 截止日期（业务身份组成部分）
    holder_num: int | None          # 股东户数


@dataclass(frozen=True, slots=True)
class ProviderShareholderBatch:
    provider_code: str
    request_date: date
    records: tuple[ProviderShareholderRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderShareholderCountBatch:
    provider_code: str
    request_date: date
    records: tuple[ProviderShareholderCountRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
    isolated: tuple[ProviderInvalidCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ShareholderHolding:
    """规范化持仓记录，身份已解析为稳定 stock_id。"""

    end_date: date
    stock_id: str
    holder_kind: str
    holder_name: str
    ann_date: date
    stock_code: str                 # 来源股票代码（ts_code，含后缀）
    hold_amount: Decimal | None
    hold_ratio: Decimal | None
    hold_float_ratio: Decimal | None
    hold_change: Decimal | None
    holder_type: str | None


@dataclass(frozen=True, slots=True)
class ShareholderCount:
    """规范化股东人数记录，身份已解析为稳定 stock_id。"""

    end_date: date
    stock_id: str
    ann_date: date
    stock_code: str
    holder_num: int | None
