"""ClickHouse ``stock_factor`` 表批量写入与内部查询 Repository。

发布语义与 data-model.md §4 一致：全批候选内存校验后以一次批量 INSERT
（单 block 原子）写入；同键替换由 ReplacingMergeTree(updated_at) 保证；
同键既有行比较按字段分级（research 决策 7 / spec FR-010/ED-009）：
- 稳定字段（stock_code、close、不复权/估值/天数字段）差异 → RECORD_CONFLICT
  整批失败，不得任意覆盖；
- 仅可修订字段（_qfq/_hfq 复权变体 + adj_factor）差异 → updated（正常数据
  修订，按来源最新值更新）；
- 无差异 → unchanged；无既有行 → added。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from lucking.clickhouse import ClickHouseClient
from lucking.models.stock_factor import (
    REVISION_ALLOWED_FIELDS,
    STOCK_FACTOR_FIELDS,
    StockFactor,
)
from lucking.repositories.market_data import MarketDataValidationError

_IDENTITY_COLUMNS = ("trade_date", "stock_id", "stock_code")
STOCK_FACTOR_TABLE = "stock_factor"
STOCK_FACTOR_COLUMNS = (
    _IDENTITY_COLUMNS + ("close",) + STOCK_FACTOR_FIELDS + ("updated_at",)
)
# 业务比较列 = 身份列之后、updated_at 之前（close 与全部白名单字段）
_BUSINESS_COLUMNS = _IDENTITY_COLUMNS[1:] + ("close",) + STOCK_FACTOR_FIELDS


class StockFactorClickHouseRepository:
    def __init__(self, client: ClickHouseClient) -> None:
        self._client = client

    def publish_batch(
        self,
        trade_date: date,
        records: Sequence[StockFactor],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        """单 block 写入全部候选，返回 (added, updated, unchanged)。

        稳定字段差异抛 ``RECORD_CONFLICT``（整批失败）；可修订字段差异按
        来源最新值更新并计 updated（spec FR-010/ED-009）。
        """
        rows = [_record_row(record, updated_at) for record in records]
        existing = self._existing_values(trade_date)
        added = updated = unchanged = 0
        for row in rows:
            previous = existing.get(_clean_id(row["stock_id"]))
            if previous is None:
                added += 1
                continue
            stable_diff = any(
                _normalize_value(row[column]) != previous.get(column)
                for column in _STABLE_COLUMNS
            )
            if stable_diff:
                raise MarketDataValidationError(
                    "RECORD_CONFLICT", "同一业务身份存在稳定字段冲突"
                )
            revision_diff = any(
                _normalize_value(row[column]) != previous.get(column)
                for column in _REVISION_COLUMNS
            )
            if revision_diff:
                updated += 1
            else:
                unchanged += 1
        self._client.insert_rows(STOCK_FACTOR_TABLE, STOCK_FACTOR_COLUMNS, rows)
        return added, updated, unchanged

    def query_stock_factors(
        self,
        *,
        stock_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("分页参数非法")
        clauses: list[str] = []
        if stock_id is not None:
            clauses.append(f"stock_id = '{stock_id}'")
        if start_date is not None:
            clauses.append(f"trade_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"trade_date <= '{end_date.isoformat()}'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._client.execute(
            f"SELECT * FROM {self._client.database}.{STOCK_FACTOR_TABLE} FINAL{where} "
            f"ORDER BY trade_date, stock_id LIMIT {limit} OFFSET {offset}"
        )
        return tuple(_clean_row(row) for row in rows)

    def count(self, trade_date: date) -> int:
        rows = self._client.execute(
            f"SELECT count() AS count FROM {self._client.database}.{STOCK_FACTOR_TABLE} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return int(rows[0]["count"])

    def _existing_values(
        self, trade_date: date
    ) -> dict[str, dict[str, Any]]:
        selected = ", ".join(_BUSINESS_COLUMNS)
        rows = self._client.execute(
            f"SELECT {selected} FROM {self._client.database}.{STOCK_FACTOR_TABLE} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return {
            _clean_id(row["stock_id"]): {
                column: _normalize_value(row[column]) for column in _BUSINESS_COLUMNS
            }
            for row in rows
        }


# 稳定字段 = 身份代码 + 行情锚点 + 白名单中非可修订字段
_STABLE_COLUMNS: tuple[str, ...] = ("stock_code", "close") + tuple(
    field for field in STOCK_FACTOR_FIELDS if field not in REVISION_ALLOWED_FIELDS
)
_REVISION_COLUMNS: tuple[str, ...] = tuple(
    field for field in STOCK_FACTOR_FIELDS if field in REVISION_ALLOWED_FIELDS
)


def _record_row(record: StockFactor, updated_at: datetime) -> dict[str, Any]:
    values: dict[str, Any] = {
        "trade_date": record.trade_date,
        "stock_id": record.stock_id,
        "stock_code": record.stock_code,
        "close": record.close,
    }
    for field in STOCK_FACTOR_FIELDS:
        values[field] = record.values.get(field)
    values["updated_at"] = updated_at
    return values


def _clean_id(value: Any) -> str:
    """FixedString(36) 读取时带 \\x00 填充，按业务语义去除。"""
    return str(value).rstrip("\x00")


def _clean_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: (_clean_id(value) if key == "stock_id" else value)
        for key, value in row.items()
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
