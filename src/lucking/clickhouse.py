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
        rows = self.execute(
            f"DESCRIBE TABLE {self._database}.{table}"
        )
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
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(
                    self._url,
                    headers=self._headers,
                    content=body,
                    params={"query": query},
                )
        except httpx.HTTPError as exc:
            raise ClickHousePersistenceError("ClickHouse 网络连接或超时错误") from exc
        if response.status_code != 200:
            summary = " ".join(response.text.split())[:200] or "ClickHouse 拒绝请求"
            raise ClickHousePersistenceError(
                summary, status_code=response.status_code
            )
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


def migrate(settings: Settings) -> list[str]:
    """创建五张 ClickHouse 业务表（幂等），返回创建的表名列表。"""
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
        return (
            value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")
        )
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lucking.clickhouse")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="创建五张 ClickHouse 业务表（幂等）")
    args = parser.parse_args(argv)
    if args.command == "migrate":
        settings = Settings()
        created = migrate(settings)
        print(f"ClickHouse migrate 完成：{', '.join(created)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
