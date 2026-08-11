"""ClickHouse session component: connection, batch insert, schema and error classification.

使用 ClickHouse HTTP 接口，基于已有 httpx 依赖，不新增运行依赖。
连接参数（主机、用户、密钥）一律通过配置对象传入，任何异常摘要
都不得包含连接串或密钥。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from lucking.config import Settings


class ClickHousePersistenceError(RuntimeError):
    """统一持久化异常；category 固定为 PERSISTENCE_ERROR。"""

    category = "PERSISTENCE_ERROR"

    def __init__(self, summary: str, *, status_code: int | None = None) -> None:
        self.summary = summary[:500]
        self.status_code = status_code
        super().__init__(f"{self.category}: {self.summary}")


@dataclass(frozen=True, slots=True)
class ClickHouseColumnInfo:
    name: str
    type: str
    default_type: str
    default_expression: str
    comment: str


class ClickHouseClient:
    """基于 ClickHouse HTTP 接口的只读/批量写入客户端。"""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        *,
        user: str = "lucking",
        password: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = f"http://{host}:{port}"
        self._database = database
        self._headers = {
            "X-ClickHouse-User": user,
            "X-ClickHouse-Database": database,
        }
        if password:
            self._headers["X-ClickHouse-Key"] = password
        self._transport = transport
        self._timeout = timeout

    @property
    def database(self) -> str:
        return self._database

    def execute(self, query: str) -> list[dict[str, Any]]:
        """执行读取查询，以 JSONEachRow 返回行。"""
        text = self._post(query + " FORMAT JSONEachRow")
        return [_parse_row(line) for line in text.splitlines() if line.strip()]

    def insert_rows(
        self, table: str, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """单 block 批量插入；行以 JSONEachRow 序列化，Decimal/date/datetime 转字符串。"""
        if not rows:
            return 0
        column_list = ", ".join(columns)
        body = (
            "\n".join(
                json.dumps(
                    {key: _json_value(value) for key, value in row.items()},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                for row in rows
            )
            + "\n"
        )
        self._post(
            f"INSERT INTO {self._database}.{table} ({column_list}) FORMAT JSONEachRow",
            body=body,
        )
        return len(rows)

    def describe_table(self, table: str) -> tuple[ClickHouseColumnInfo, ...]:
        rows = self.execute(f"DESCRIBE TABLE {self._database}.{table}")
        return tuple(
            ClickHouseColumnInfo(
                str(row.get("name", "")),
                str(row.get("type", "")),
                str(row.get("default_type", "")),
                str(row.get("default_expression", "")),
                str(row.get("comment", "")),
            )
            for row in rows
        )

    def table_engine(self, table: str) -> str:
        rows = self.execute(
            "SELECT engine FROM system.tables "
            f"WHERE database = '{self._database}' AND name = '{table}'"
        )
        if not rows:
            raise ClickHousePersistenceError(f"ClickHouse 表不存在：{table}")
        return str(rows[0]["engine"])

    def table_partition_key(self, table: str) -> str:
        rows = self.execute(
            "SELECT partition_key FROM system.tables "
            f"WHERE database = '{self._database}' AND name = '{table}'"
        )
        if not rows:
            raise ClickHousePersistenceError(f"ClickHouse 表不存在：{table}")
        return str(rows[0]["partition_key"])

    def table_sorting_key(self, table: str) -> str:
        rows = self.execute(
            "SELECT sorting_key FROM system.tables "
            f"WHERE database = '{self._database}' AND name = '{table}'"
        )
        if not rows:
            raise ClickHousePersistenceError(f"ClickHouse 表不存在：{table}")
        return str(rows[0]["sorting_key"])

    def execute_ddl(self, query: str) -> None:
        """执行 DDL 语句（不附加 FORMAT）。"""
        self._post(query)

    def _post(self, query: str, *, body: str | None = None) -> str:
        """执行 SQL；无请求体（读查询）时把 SQL 放入请求体而非 URL query。

        之前把 SQL 放进 URL query 串：批量键条件（如股东 ``_existing_*`` 的
        数百个 OR 子句，中文股东名经百分号编码后体积约 ×3）会超过 httpx
        的 65536 字节 URL 上限（实测 2026-08-06 ``InvalidURL: URL
        component 'query' too long``）。INSERT 的 SQL 很短，保留 URL query，
        数据仍在请求体。
        """
        try:
            # ClickHouse is an internal database endpoint. Environment HTTP
            # proxies must never intercept localhost/private persistence calls.
            with httpx.Client(
                transport=self._transport, timeout=self._timeout, trust_env=False
            ) as client:
                response = client.post(
                    self._url,
                    headers=self._headers,
                    content=query if body is None else body,
                    params=None if body is None else {"query": query},
                )
        except httpx.HTTPError as exc:
            raise ClickHousePersistenceError("ClickHouse 网络连接或超时错误") from exc
        if response.status_code != 200:
            summary = " ".join(response.text.split())[:200] or "ClickHouse 拒绝请求"
            raise ClickHousePersistenceError(summary, status_code=response.status_code)
        return response.text


def build_clickhouse_client(settings: Settings) -> ClickHouseClient:
    password = (
        settings.clickhouse_password.get_secret_value()
        if settings.clickhouse_password is not None
        else None
    )
    return ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=password,
    )


LIMIT_STATUS_COMMENT = (
    "涨跌停状态：0平盘、1涨停、2跌停、3炸板、4跌停打开、5跳水、6一字涨停、7一字跌停"
)


def _kline_ddl(table: str, table_comment: str, trade_date_comment: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {{database}}.{table}
(
    trade_date Date NOT NULL COMMENT '{trade_date_comment}',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    venue_code FixedString(4) NOT NULL COMMENT '规范交易场所代码：XSHG、XSHE或XBSE',
    security_code String NOT NULL COMMENT '来源明确返回的规范证券代码',
    open Decimal(12,4) NOT NULL COMMENT '未复权周期开盘价',
    high Decimal(12,4) NOT NULL COMMENT '未复权周期最高价',
    low Decimal(12,4) NOT NULL COMMENT '未复权周期最低价',
    close Decimal(12,4) NOT NULL COMMENT '未复权周期收盘价',
    vol Decimal(24,2) NOT NULL COMMENT '周期成交量（手）',
    amount Decimal(24,2) NOT NULL COMMENT '周期成交额（千元）',
    change Decimal(12,4) NOT NULL COMMENT '周期涨跌额',
    pct_chg Decimal(8,3) NOT NULL COMMENT '周期涨跌幅（百分比）',
    end_date Nullable(Date) COMMENT '来源计算截至日期；与trade_date一致时为空',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
COMMENT '{table_comment}'
"""


# 五张业务表 DDL：与 data-model.md §3-7 完全一致，
# 引擎注释与 SHOW CREATE TABLE 必须一致（迁移与 schema 校验共用）。
CLICKHOUSE_TABLE_DDL: dict[str, str] = {
    "daily_quote": """
CREATE TABLE IF NOT EXISTS {database}.daily_quote
(
    trade_date Date NOT NULL COMMENT '交易日',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    venue_code FixedString(4) NOT NULL COMMENT '规范交易场所代码：XSHG、XSHE或XBSE',
    security_code String NOT NULL COMMENT '来源明确返回的规范证券代码',
    open Decimal(12,4) NOT NULL COMMENT '未复权开盘价',
    high Decimal(12,4) NOT NULL COMMENT '未复权最高价',
    low Decimal(12,4) NOT NULL COMMENT '未复权最低价',
    close Decimal(12,4) NOT NULL COMMENT '未复权收盘价',
    pre_close Decimal(12,4) NOT NULL COMMENT '昨收价（除权后）',
    change Decimal(12,4) NOT NULL COMMENT '涨跌额',
    pct_chg Decimal(8,3) NOT NULL COMMENT '涨跌幅（百分比，基于除权昨收）',
    vol Decimal(24,2) NOT NULL COMMENT '成交量（手）',
    amount Decimal(24,2) NOT NULL COMMENT '成交额（千元）',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
COMMENT 'A股日线行情'
""",
    "adj_factor": """
CREATE TABLE IF NOT EXISTS {database}.adj_factor
(
    trade_date Date NOT NULL COMMENT '交易日',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    venue_code FixedString(4) NOT NULL COMMENT '规范交易场所代码：XSHG、XSHE或XBSE',
    security_code String NOT NULL COMMENT '来源明确返回的规范证券代码',
    adj_factor Decimal(20,6) NOT NULL COMMENT '当日复权因子',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
COMMENT 'A股日线复权因子'
""",
    "daily_basic": f"""
CREATE TABLE IF NOT EXISTS {{database}}.daily_basic
(
    trade_date Date NOT NULL COMMENT '交易日',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    venue_code FixedString(4) NOT NULL COMMENT '规范交易场所代码：XSHG、XSHE或XBSE',
    security_code String NOT NULL COMMENT '来源明确返回的规范证券代码',
    pe Nullable(Decimal(16,4)) COMMENT '市盈率（亏损公司为空）',
    pe_ttm Nullable(Decimal(16,4)) COMMENT '市盈率TTM（亏损公司为空）',
    pb Nullable(Decimal(16,4)) COMMENT '市净率（亏损公司为空）',
    ps Nullable(Decimal(16,4)) COMMENT '市销率',
    ps_ttm Nullable(Decimal(16,4)) COMMENT '市销率TTM',
    dv_ratio Nullable(Decimal(12,4)) COMMENT '股息率',
    dv_ttm Nullable(Decimal(12,4)) COMMENT '股息率TTM',
    total_share Nullable(Decimal(24,4)) COMMENT '总股本（万股）',
    float_share Nullable(Decimal(24,4)) COMMENT '流通股本（万股）',
    free_share Nullable(Decimal(24,4)) COMMENT '自由流通股本（万股）',
    total_mv Nullable(Decimal(24,4)) COMMENT '总市值（万元）',
    circ_mv Nullable(Decimal(24,4)) COMMENT '流通市值（万元）',
    turnover_rate Nullable(Decimal(12,4)) COMMENT '换手率（百分比）',
    turnover_rate_f Nullable(Decimal(12,4)) COMMENT '自由流通换手率（百分比）',
    volume_ratio Nullable(Decimal(12,4)) COMMENT '量比',
    limit_status Nullable(UInt8) COMMENT '{LIMIT_STATUS_COMMENT}',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
COMMENT 'A股每日基本面指标'
""",
    "weekly_kline": _kline_ddl(  # noqa: E501
        "weekly_kline", "A股周K线", "周期最后交易日（每周五或该周最后交易日）"
    ),
    "monthly_kline": _kline_ddl(  # noqa: E501
        "monthly_kline", "A股月K线", "周期最后交易日（月末最后一个交易日）"
    ),
}


# 指数技术因子 78 列（来源字段去掉 _bfq 后缀；均不复权，缺失以 NULL 保存）。
# 与 data-model.md §3.4、tushare-index-factor.md §3 一致。
_INDEX_FACTOR_COLUMNS: list[tuple[str, str]] = [
    *[(f"ma_{n}", f"简单移动平均（{n} 日）") for n in (5, 10, 20, 30, 60, 90, 250)],
    *[(f"ema_{n}", f"指数移动平均（{n} 日）") for n in (5, 10, 20, 30, 60, 90, 250)],
    ("expma_12", "指数平均数（12 日）"),
    ("expma_50", "指数平均数（50 日）"),
    ("bbi", "BBI 多空指标"),
    ("macd", "MACD 值"),
    ("macd_dea", "MACD 信号线（DEA）"),
    ("macd_dif", "MACD 差离值（DIF）"),
    ("kdj", "KDJ 随机指标"),
    ("kdj_k", "KDJ K 线"),
    ("kdj_d", "KDJ D 线"),
    *[(f"rsi_{n}", f"相对强弱指标 RSI（{n} 日）") for n in (6, 12, 24)],
    ("cci", "CCI 顺势指标"),
    ("wr", "威廉指标（WR）"),
    ("wr1", "威廉指标（WR1）"),
    ("bias1", "BIAS 乖离率（1）"),
    ("bias2", "BIAS 乖离率（2）"),
    ("bias3", "BIAS 乖离率（3）"),
    ("psy", "心理线 PSY"),
    ("psyma", "心理线均值"),
    ("roc", "变动率 ROC"),
    ("maroc", "变动率均值"),
    ("mfi", "MFI 资金流量指标"),
    ("mtm", "动量指标 MTM"),
    ("mtmma", "动量指标均值"),
    ("boll_lower", "布林带下轨"),
    ("boll_mid", "布林中轨"),
    ("boll_upper", "布林带上轨"),
    ("ktn_down", "肯特纳通道下轨"),
    ("ktn_mid", "肯特纳通道中轨"),
    ("ktn_upper", "肯特纳通道上轨"),
    ("taq_up", "唐安奇（海龟）通道上轨"),
    ("taq_mid", "唐安奇（海龟）通道中轨"),
    ("taq_down", "唐安奇（海龟）通道下轨"),
    ("xsii_td1", "薛斯通道 II（1）"),
    ("xsii_td2", "薛斯通道 II（2）"),
    ("xsii_td3", "薛斯通道 II（3）"),
    ("xsii_td4", "薛斯通道 II（4）"),
    ("dmi_pdi", "动向指标 +DI"),
    ("dmi_mdi", "动向指标 -DI"),
    ("dmi_adx", "动向指标 ADX"),
    ("dmi_adxr", "动向指标 ADXR"),
    ("obv", "能量潮 OBV"),
    ("vr", "VR 容量比率"),
    ("emv", "简易波动 EMV"),
    ("maemv", "简易波动均值"),
    ("cr", "CR 价格动量"),
    ("brar_ar", "BRAR 情绪指标 AR"),
    ("brar_br", "BRAR 情绪指标 BR"),
    ("dpo", "区间震荡线 DPO"),
    ("madpo", "区间震荡线均值"),
    ("dfma_dif", "平行线差"),
    ("dfma_difma", "平行线差均值"),
    ("asi", "振动升降指标 ASI"),
    ("asit", "振动升降指标均值"),
    ("atr", "真实波幅均值 ATR"),
    ("mass", "梅斯线 MASS"),
    ("ma_mass", "梅斯线均值"),
    ("trix", "三重指数平滑 TRIX"),
    ("trma", "三重指数平滑均值"),
]

_FACTOR_DECIMAL_COLUMNS = "".join(
    f"    {name} Nullable(Decimal(12,4)) COMMENT '{comment}',\n"
    for name, comment in _INDEX_FACTOR_COLUMNS
)
_FACTOR_UINT_COLUMNS = "".join(
    f"    {name} Nullable(UInt16) COMMENT '{comment}',\n"
    for name, comment in (
        ("updays", "连涨天数"),
        ("downdays", "连跌天数"),
        ("topdays", "当前最高价是近 N 周期内最高价的最大值（N 为周期数）"),
        ("lowdays", "当前最低价是近 N 周期内最低价的最小值（N 为周期数）"),
    )
)


def _index_factor_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {{database}}.index_factor
(
    trade_date Date NOT NULL COMMENT '交易日',
    index_id FixedString(36) NOT NULL COMMENT '项目规范指数业务UUID',
    index_code String NOT NULL COMMENT '规范指数代码（来源 ts_code，含后缀）',
    open Nullable(Decimal(12,4)) COMMENT '开盘价（部分指数族不提供）',
    high Nullable(Decimal(12,4)) COMMENT '最高价（部分指数族不提供）',
    low Nullable(Decimal(12,4)) COMMENT '最低价（部分指数族不提供）',
    close Decimal(12,4) NOT NULL COMMENT '收盘价（行情锚点）',
    pre_close Nullable(Decimal(12,4)) COMMENT '昨收价（新基日指数等缺失）',
    change Nullable(Decimal(12,4)) COMMENT '涨跌额',
    pct_chg Nullable(Decimal(12,4)) COMMENT '涨跌幅（百分比）',
    vol Nullable(Decimal(24,2)) COMMENT '成交量（手）',
    amount Nullable(Decimal(24,2)) COMMENT '成交额（千元）',
{_FACTOR_DECIMAL_COLUMNS}{_FACTOR_UINT_COLUMNS}
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, index_id)
COMMENT '指数每日技术因子（不复权，含基础行情）'
"""


CLICKHOUSE_TABLE_DDL["index_factor"] = _index_factor_ddl()


# 股票技术面因子宽表：行情/估值/技术指标及全部复权变体（字段名原样保留
# _bfq/_qfq/_hfq 后缀），与 data-model.md §3、tushare-stock-factor.md §3、
# models/stock_factor.py 白名单一致（research 决策 7）。
from lucking.models.stock_factor import (  # noqa: E402
    DAY_COUNT_FIELDS,
    STOCK_FACTOR_FIELDS,
)

_PRICE_VARIANT_COMMENT = {
    "bfq": "不复权",
    "qfq": "前复权",
    "hfq": "后复权",
}
_QUOTE_FIELD_COMMENTS = {
    "pre_close": "昨收价",
    "change": "涨跌额",
    "pct_chg": "涨跌幅（%，除权后口径）",
    "vol": "成交量（手）",
    "amount": "成交额（千元）",
    "turnover_rate": "换手率（%）",
    "turnover_rate_f": "自由流通换手率（%）",
    "volume_ratio": "量比",
    "adj_factor": "复权因子（累计，随后续除权除息重算）",
}
_VALUATION_FIELD_COMMENTS = {
    "pe": "市盈率（动态）",
    "pe_ttm": "市盈率（滚动 TTM）",
    "pb": "市净率",
    "ps": "市销率（动态）",
    "ps_ttm": "市销率（滚动 TTM）",
    "dv_ratio": "股息率（静态）",
    "dv_ttm": "股息率（滚动 TTM）",
    "total_share": "总股本（万股）",
    "float_share": "流通股本（万股）",
    "free_share": "自由流通股本（万股）",
    "total_mv": "总市值（万元）",
    "circ_mv": "流通市值（万元）",
}
_INDICATOR_BASE_COMMENTS = {
    "asi": "振动升降指标 ASI",
    "asit": "振动升降指标均值",
    "atr": "真实波幅均值 ATR",
    "bbi": "BBI 多空指标",
    "bias1": "BIAS 乖离率（1）",
    "bias2": "BIAS 乖离率（2）",
    "bias3": "BIAS 乖离率（3）",
    "boll_lower": "布林带下轨",
    "boll_mid": "布林中轨",
    "boll_upper": "布林带上轨",
    "brar_ar": "BRAR 情绪指标 AR",
    "brar_br": "BRAR 情绪指标 BR",
    "cci": "CCI 顺势指标",
    "cr": "CR 价格动量",
    "dfma_dif": "平行线差",
    "dfma_difma": "平行线差均值",
    "dmi_pdi": "动向指标 +DI",
    "dmi_mdi": "动向指标 -DI",
    "dmi_adx": "动向指标 ADX",
    "dmi_adxr": "动向指标 ADXR",
    "dpo": "区间震荡线 DPO",
    "madpo": "区间震荡线均值",
    "emv": "简易波动 EMV",
    "maemv": "简易波动均值",
    "kdj": "KDJ 随机指标",
    "kdj_k": "KDJ K 线",
    "kdj_d": "KDJ D 线",
    "ktn_upper": "肯特纳通道上轨",
    "ktn_mid": "肯特纳通道中轨",
    "ktn_down": "肯特纳通道下轨",
    "macd": "MACD 值",
    "macd_dea": "MACD 信号线（DEA）",
    "macd_dif": "MACD 差离值（DIF）",
    "mass": "梅斯线 MASS",
    "ma_mass": "梅斯线均值",
    "mfi": "MFI 资金流量指标",
    "mtm": "动量指标 MTM",
    "mtmma": "动量指标均值",
    "obv": "能量潮 OBV",
    "psy": "心理线 PSY",
    "psyma": "心理线均值",
    "roc": "变动率 ROC",
    "maroc": "变动率均值",
    "taq_up": "唐安奇（海龟）通道上轨",
    "taq_mid": "唐安奇（海龟）通道中轨",
    "taq_down": "唐安奇（海龟）通道下轨",
    "trix": "三重指数平滑 TRIX",
    "trma": "三重指数平滑均值",
    "vr": "VR 容量比率",
    "wr": "威廉指标（WR）",
    "wr1": "威廉指标（WR1）",
    "xsii_td1": "薛斯通道 II（1）",
    "xsii_td2": "薛斯通道 II（2）",
    "xsii_td3": "薛斯通道 II（3）",
    "xsii_td4": "薛斯通道 II（4）",
}
# 周期型指标（ma/ema/expma/rsi）由基名携带周期，注释直接使用基名。
_INDICATOR_BASE_COMMENTS.update(
    {f"ma_{n}": f"简单移动平均（{n} 日）" for n in (5, 10, 20, 30, 60, 90, 250)}
)
_INDICATOR_BASE_COMMENTS.update(
    {f"ema_{n}": f"指数移动平均（{n} 日）" for n in (5, 10, 20, 30, 60, 90, 250)}
)
_INDICATOR_BASE_COMMENTS.update({f"expma_{n}": f"指数平均数（{n} 日）" for n in (12, 50)})
_INDICATOR_BASE_COMMENTS.update({f"rsi_{n}": f"相对强弱指标 RSI（{n} 日）" for n in (6, 12, 24)})
_DAY_COUNT_COMMENTS = {
    "updays": "连涨天数",
    "downdays": "连跌天数",
    "lowdays": "区间最低价天数",
    "topdays": "区间最高价天数",
}


_PRICE_NAMES = {"open": "开盘价", "high": "最高价", "low": "最低价", "close": "收盘价"}


def _stock_factor_field_comment(field: str) -> str:
    """按字段名生成中文列注释（与 data-model.md §3 口径一致）。

    变体命名两种形态：前缀式周期（ma_bfq_5）与后缀式变体（kdj_bfq）；
    先解析前缀式，再解析后缀式，最后按基名查表。
    """
    if field in _QUOTE_FIELD_COMMENTS:
        return _QUOTE_FIELD_COMMENTS[field]
    if field in _VALUATION_FIELD_COMMENTS:
        return _VALUATION_FIELD_COMMENTS[field]
    if field in _DAY_COUNT_COMMENTS:
        return _DAY_COUNT_COMMENTS[field]
    if field in _PRICE_NAMES:
        return _PRICE_NAMES[field]
    # 价格复权变体（open_qfq / close_hfq ...）
    price_base, _, variant = field.partition("_")
    if price_base in _PRICE_NAMES and variant in _PRICE_VARIANT_COMMENT:
        return f"{_PRICE_NAMES[price_base]}（{_PRICE_VARIANT_COMMENT[variant]}）"
    # 前缀式周期指标变体（ma_bfq_5 → 基名 ma_5）
    family, rest = field.split("_", 1)
    if family in {"ma", "ema", "rsi"}:
        variant, _, period = rest.partition("_")
        base = f"{family}_{period}"
        if base in _INDICATOR_BASE_COMMENTS:
            suffix = _PRICE_VARIANT_COMMENT.get(variant, variant)
            return f"{_INDICATOR_BASE_COMMENTS[base]}（{suffix}）"
    # 后缀式变体（kdj_bfq → 基名 kdj）
    base, _, variant = field.rpartition("_")
    if base in _INDICATOR_BASE_COMMENTS and variant in _PRICE_VARIANT_COMMENT:
        return f"{_INDICATOR_BASE_COMMENTS[base]}（{_PRICE_VARIANT_COMMENT[variant]}）"
    return field


# 股本/市值类字段使用宽精度 Decimal(24,4)（与 005 daily_basic 约定一致）：
# 大市值股票 total_mv 可达 1 万亿元（约 10^8 万元，Decimal(12,4) 溢出，
# 实测 2026-08-05 回补报 "Decimal value is too big"）。
_VALUATION_WIDE_FIELDS: frozenset[str] = frozenset(
    {"total_share", "float_share", "free_share", "total_mv", "circ_mv"}
)


def _stock_factor_columns() -> str:
    lines = []
    for field in STOCK_FACTOR_FIELDS:
        if field in DAY_COUNT_FIELDS:
            column_type = "Nullable(UInt16)"
        elif field in _VALUATION_WIDE_FIELDS:
            column_type = "Nullable(Decimal(24,4))"
        else:
            column_type = "Nullable(Decimal(12,4))"
        lines.append(f"    {field} {column_type} COMMENT '{_stock_factor_field_comment(field)}',")
    return "\n".join(lines)


def _stock_factor_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {{database}}.stock_factor
(
    trade_date Date NOT NULL COMMENT '交易日',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    stock_code String NOT NULL COMMENT '来源股票代码（ts_code，含后缀）',
    close Decimal(12,4) NOT NULL COMMENT '收盘价（行情锚点，除权前口径）',
{_stock_factor_columns()}
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
COMMENT 'A股每日技术面因子（行情/估值/技术指标及全部复权变体）'
"""


CLICKHOUSE_TABLE_DDL["stock_factor"] = _stock_factor_ddl()


# 股东数据两张业务表（008-sync-shareholder-data，data-model.md §3）：
# - shareholder_holding：前十大股东与前十大流通股东统一表，holder_kind 判别；
#   行身份 = (end_date, stock_id, holder_kind, holder_name)，更正公告按
#   updated_at 收敛为最新公告值（spec FR-010/ED-010）。
# - shareholder_count：股东人数；行身份 = (end_date, stock_id)。
# 均按披露期（end_date）月份分区，无 TTL，长期保留（NFR-009）。


def _shareholder_holding_ddl() -> str:
    return """
CREATE TABLE IF NOT EXISTS {database}.shareholder_holding
(
    end_date Date NOT NULL COMMENT '披露期（报告期/截止日期）',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    holder_kind Enum8('TOP10' = 1, 'TOP10_FLOAT' = 2) NOT NULL COMMENT '股东名单类型',
    holder_name String NOT NULL COMMENT '股东名称',
    ann_date Date NOT NULL COMMENT '公告日期（最新公告覆盖旧值）',
    stock_code String NOT NULL COMMENT '来源股票代码（ts_code，含后缀）',
    hold_amount Nullable(Decimal(24,2)) COMMENT '持有数量（股）',
    hold_ratio Nullable(Decimal(12,4)) COMMENT '占总股本比例（%）',
    hold_float_ratio Nullable(Decimal(12,4)) COMMENT '占流通股本比例（%）',
    hold_change Nullable(Decimal(24,2)) COMMENT '持股变动（股，可为负）',
    holder_type Nullable(String) COMMENT '股东类型（一般企业、自然人、保险投资组合等）',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(end_date)
ORDER BY (end_date, stock_id, holder_kind, holder_name)
COMMENT 'A股前十大股东与前十大流通股东持仓（按披露期）'
"""


def _shareholder_count_ddl() -> str:
    return """
CREATE TABLE IF NOT EXISTS {database}.shareholder_count
(
    end_date Date NOT NULL COMMENT '截止日期（股东户数统计日）',
    stock_id FixedString(36) NOT NULL COMMENT '项目规范股票业务UUID',
    ann_date Date NOT NULL COMMENT '公告日期（最新公告覆盖旧值）',
    stock_code String NOT NULL COMMENT '来源股票代码（ts_code，含后缀）',
    holder_num Nullable(UInt32) COMMENT '股东户数',
    updated_at DateTime64(3) NOT NULL COMMENT '最近写入UTC时间（版本列）'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(end_date)
ORDER BY (end_date, stock_id)
COMMENT 'A股股东户数（按统计截止日）'
"""


CLICKHOUSE_TABLE_DDL["shareholder_holding"] = _shareholder_holding_ddl()
CLICKHOUSE_TABLE_DDL["shareholder_count"] = _shareholder_count_ddl()


def migrate(settings: Settings) -> list[str]:
    """幂等创建全部 ClickHouse 业务表，返回创建的表名列表。"""
    client = build_clickhouse_client(settings)
    created: list[str] = []
    for name, ddl in CLICKHOUSE_TABLE_DDL.items():
        client.execute_ddl(ddl.format(database=settings.clickhouse_database))
        created.append(name)
    return created


def _parse_row(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ClickHousePersistenceError("ClickHouse 响应不是有效 JSON 行") from exc
    if not isinstance(value, dict):
        raise ClickHousePersistenceError("ClickHouse 响应行结构非法")
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lucking.clickhouse")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="创建八张 ClickHouse 业务表（幂等）")
    args = parser.parse_args(argv)
    if args.command == "migrate":
        settings = Settings()
        created = migrate(settings)
        print(f"ClickHouse migrate 完成：{', '.join(created)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
