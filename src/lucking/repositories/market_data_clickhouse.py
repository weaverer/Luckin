"""ClickHouse 行情业务表批量写入与内部查询 Repository。

发布语义：全批候选内存校验后，以一次批量 INSERT（单 block 原子）写入对应
业务表；同键替换由 ReplacingMergeTree(updated_at) 保证（data-model.md §12）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from lucking.clickhouse import ClickHouseClient
from lucking.models.market_data import DataKind

_IDENTITY_COLUMNS = ("trade_date", "stock_id", "venue_code", "security_code")

TABLE_BY_KIND: dict[DataKind, str] = {
    DataKind.DAILY_QUOTE: "daily_quote",
    DataKind.ADJ_FACTOR: "adj_factor",
    DataKind.DAILY_BASIC: "daily_basic",
    DataKind.WEEKLY_KLINE: "weekly_kline",
    DataKind.MONTHLY_KLINE: "monthly_kline",
}

_DAILY_QUOTE_COLUMNS = _IDENTITY_COLUMNS + (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "updated_at",
)
_ADJ_FACTOR_COLUMNS = _IDENTITY_COLUMNS + ("adj_factor", "updated_at")
_DAILY_BASIC_COLUMNS = _IDENTITY_COLUMNS + (
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
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "limit_status",
    "updated_at",
)
_KLINE_COLUMNS = _IDENTITY_COLUMNS + (
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "change",
    "pct_chg",
    "end_date",
    "updated_at",
)

COLUMNS_BY_KIND: dict[DataKind, tuple[str, ...]] = {
    DataKind.DAILY_QUOTE: _DAILY_QUOTE_COLUMNS,
    DataKind.ADJ_FACTOR: _ADJ_FACTOR_COLUMNS,
    DataKind.DAILY_BASIC: _DAILY_BASIC_COLUMNS,
    DataKind.WEEKLY_KLINE: _KLINE_COLUMNS,
    DataKind.MONTHLY_KLINE: _KLINE_COLUMNS,
}


class MarketDataClickHouseRepository:
    def __init__(self, client: ClickHouseClient) -> None:
        self._client = client

    def publish_batch(
        self,
        data_kind: DataKind,
        trade_date: date,
        records: Sequence[Any],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        """单 block 写入全部候选，返回 (added, updated, unchanged)。

        added/updated/unchanged 通过与既有行集（FINAL 去重后）比较业务字段
        得出；ClickHouse 不提供行级 UPDATE，计数只用于审计。
        """
        table = TABLE_BY_KIND[data_kind]
        columns = COLUMNS_BY_KIND[data_kind]
        rows = [_record_row(record, updated_at) for record in records]
        # 周/月线以记录自身的周期最后交易日为准（可早于请求交易日）
        actual_trade_date = rows[0]["trade_date"] if rows else trade_date
        existing = self._existing_digests(table, actual_trade_date, columns)
        added = updated = unchanged = 0
        for row in rows:
            digest = _business_digest(row, columns)
            previous = existing.get(row["stock_id"])
            if previous is None:
                added += 1
            elif previous == digest:
                unchanged += 1
            else:
                updated += 1
        self._client.insert_rows(table, columns, rows)
        return added, updated, unchanged

    def query(
        self,
        data_kind: DataKind,
        *,
        trade_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        stock_id: str | None = None,
        venue_code: str | None = None,
        security_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
        descending: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        if not 1 <= limit <= 400 or offset < 0:
            raise ValueError("分页参数非法")
        table = TABLE_BY_KIND[data_kind]
        clauses: list[str] = []
        if trade_date is not None:
            clauses.append(f"trade_date = '{trade_date.isoformat()}'")
        if start_date is not None:
            clauses.append(f"trade_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"trade_date <= '{end_date.isoformat()}'")
        if stock_id is not None:
            clauses.append(f"stock_id = '{stock_id}'")
        if venue_code is not None:
            clauses.append(f"venue_code = '{venue_code}'")
        if security_code is not None:
            clauses.append(f"security_code = '{security_code}'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = " DESC" if descending else ""
        rows = self._client.execute(
            f"SELECT * FROM {self._client.database}.{table} FINAL{where} "
            f"ORDER BY trade_date{direction}, stock_id LIMIT {limit} OFFSET {offset}"
        )
        return tuple(_clean_row(row) for row in rows)

    def count(self, data_kind: DataKind, trade_date: date) -> int:
        table = TABLE_BY_KIND[data_kind]
        rows = self._client.execute(
            f"SELECT count() AS count FROM {self._client.database}.{table} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return int(rows[0]["count"])

    def query_daily_quotes_post_adjusted(
        self,
        *,
        stock_id: str,
        limit: int = 120,
        start_date: date | None = None,
        end_date: date | None = None,
        descending: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        """查询后复权价格，成交量/成交额仍保持来源口径。

        复权在 ClickHouse 查询侧完成，避免将全历史行情和复权因子加载到应用内存。
        后复权基准取该股票最早有效复权因子；因子按交易日关联，支持除权后的历史回溯。
        """
        if not 1 <= limit <= 400:
            raise ValueError("查询条数必须为 1 至 400")
        clauses = [f"q.stock_id = '{stock_id}'"]
        if start_date is not None:
            clauses.append(f"q.trade_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"q.trade_date <= '{end_date.isoformat()}'")
        direction = "DESC" if descending else "ASC"
        where = " AND ".join(clauses)
        rows = self._client.execute(
            f"""
            WITH first_factor AS (
                SELECT argMinIf(adj_factor, trade_date, adj_factor > 0) AS factor_first
                FROM {self._client.database}.adj_factor FINAL
                WHERE stock_id = '{stock_id}'
            )
            SELECT
                q.trade_date AS trade_date,
                q.stock_id AS stock_id,
                q.venue_code AS venue_code,
                q.security_code AS security_code,
                q.open * f.adj_factor / ff.factor_first AS open,
                q.high * f.adj_factor / ff.factor_first AS high,
                q.low * f.adj_factor / ff.factor_first AS low,
                q.close * f.adj_factor / ff.factor_first AS close,
                q.pre_close * f.adj_factor / ff.factor_first AS pre_close,
                q.change AS change,
                q.pct_chg AS pct_chg,
                q.vol AS vol,
                q.amount AS amount,
                q.updated_at AS updated_at
            FROM (
                SELECT *
                FROM {self._client.database}.daily_quote FINAL
                WHERE stock_id = '{stock_id}'
            ) AS q
            INNER JOIN (
                SELECT stock_id, trade_date, adj_factor
                FROM {self._client.database}.adj_factor FINAL
                WHERE stock_id = '{stock_id}'
            ) AS f
                ON q.stock_id = f.stock_id AND q.trade_date = f.trade_date
            CROSS JOIN first_factor AS ff
            WHERE {where} AND f.adj_factor > 0 AND ff.factor_first > 0
            ORDER BY q.trade_date {direction}
            LIMIT {limit}
            """
        )
        return tuple(_clean_row(row) for row in rows)

    def _existing_digests(
        self, table: str, trade_date: date, columns: tuple[str, ...]
    ) -> dict[str, tuple[Any, ...]]:
        business = columns[4:-1]
        selected = ", ".join(("stock_id", *business))
        rows = self._client.execute(
            f"SELECT {selected} FROM {self._client.database}.{table} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return {
            _clean_stock_id(row["stock_id"]): tuple(
                _normalize_value(row[column]) for column in business
            )
            for row in rows
        }


def _record_row(record: Any, updated_at: datetime) -> dict[str, Any]:
    values: dict[str, Any] = {
        "trade_date": record.trade_date,
        "stock_id": record.stock_id,
        "venue_code": record.venue_code.value,
        "security_code": record.security_code,
    }
    for field in (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
        "adj_factor",
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
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "limit_status",
        "end_date",
    ):
        if hasattr(record, field):
            values[field] = getattr(record, field)
    values["updated_at"] = updated_at
    return values


def _business_digest(row: Mapping[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    business = columns[4:-1]
    return tuple(_normalize_value(row[column]) for column in business)


def _clean_stock_id(value: Any) -> str:
    """FixedString(36) 读取时带 \\x00 填充，按业务语义去除。"""
    return str(value).rstrip("\x00")


def _clean_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: (_clean_stock_id(value) if key == "stock_id" else value) for key, value in row.items()
    }


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value)
