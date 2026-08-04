"""ClickHouse ``index_factor`` 表批量写入与内部查询 Repository。

发布语义与 data-model.md §5 一致：全批候选内存校验后以一次批量 INSERT
（单 block 原子）写入；同键替换由 ReplacingMergeTree(updated_at) 保证；
added/updated/unchanged 通过与既有行集（FINAL）比较业务字段得出（仅审计）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from lucking.clickhouse import ClickHouseClient
from lucking.models.index_factor import FACTOR_FIELDS, IndexFactor

_IDENTITY_COLUMNS = ("trade_date", "index_id", "index_code")
_BASE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
INDEX_FACTOR_TABLE = "index_factor"
INDEX_FACTOR_COLUMNS = (
    _IDENTITY_COLUMNS + _BASE_COLUMNS + FACTOR_FIELDS + ("updated_at",)
)
# 业务比较列 = 身份列之后、updated_at 之前
_BUSINESS_COLUMNS = _IDENTITY_COLUMNS[1:] + _BASE_COLUMNS + FACTOR_FIELDS


class IndexFactorClickHouseRepository:
    def __init__(self, client: ClickHouseClient) -> None:
        self._client = client

    def publish_batch(
        self,
        trade_date: date,
        records: Sequence[IndexFactor],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        """单 block 写入全部候选，返回 (added, updated, unchanged)。"""
        rows = [_record_row(record, updated_at) for record in records]
        existing = self._existing_digests(trade_date)
        added = updated = unchanged = 0
        for row in rows:
            digest = _business_digest(row)
            previous = existing.get(row["index_id"])
            if previous is None:
                added += 1
            elif previous == digest:
                unchanged += 1
            else:
                updated += 1
        self._client.insert_rows(INDEX_FACTOR_TABLE, INDEX_FACTOR_COLUMNS, rows)
        return added, updated, unchanged

    def query(
        self,
        *,
        index_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Mapping[str, Any], ...]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("分页参数非法")
        clauses: list[str] = []
        if index_id is not None:
            clauses.append(f"index_id = '{index_id}'")
        if start_date is not None:
            clauses.append(f"trade_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"trade_date <= '{end_date.isoformat()}'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._client.execute(
            f"SELECT * FROM {self._client.database}.{INDEX_FACTOR_TABLE} FINAL{where} "
            f"ORDER BY trade_date, index_id LIMIT {limit} OFFSET {offset}"
        )
        return tuple(_clean_row(row) for row in rows)

    def count(self, trade_date: date) -> int:
        rows = self._client.execute(
            f"SELECT count() AS count FROM {self._client.database}.{INDEX_FACTOR_TABLE} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return int(rows[0]["count"])

    def _existing_digests(
        self, trade_date: date
    ) -> dict[str, tuple[Any, ...]]:
        selected = ", ".join(_BUSINESS_COLUMNS)
        rows = self._client.execute(
            f"SELECT {selected} FROM {self._client.database}.{INDEX_FACTOR_TABLE} FINAL "
            f"WHERE trade_date = '{trade_date.isoformat()}'"
        )
        return {
            _clean_index_id(row["index_id"]): tuple(
                _normalize_value(row[column]) for column in _BUSINESS_COLUMNS
            )
            for row in rows
        }


def _record_row(record: IndexFactor, updated_at: datetime) -> dict[str, Any]:
    values: dict[str, Any] = {
        "trade_date": record.trade_date,
        "index_id": record.index_id,
        "index_code": record.index_code,
    }
    for field in _BASE_COLUMNS + FACTOR_FIELDS:
        values[field] = getattr(record, field)
    values["updated_at"] = updated_at
    return values


def _business_digest(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(_normalize_value(row[column]) for column in _BUSINESS_COLUMNS)


def _clean_index_id(value: Any) -> str:
    """FixedString(36) 读取时带 \\x00 填充，按业务语义去除。"""
    return str(value).rstrip("\x00")


def _clean_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: (_clean_index_id(value) if key == "index_id" else value)
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
