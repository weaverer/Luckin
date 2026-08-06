"""ClickHouse 股东数据两表批量写入、水位计算与内部查询 Repository。

发布语义与 data-model.md §4 一致：全批候选内存校验后以一次批量 INSERT
（单 block 原子）写入；同键替换由 ReplacingMergeTree(updated_at) 保证。
修订 vs 冲突按 ``ann_date`` 锚点判定（spec FR-010/ED-010）：
- 值不同且**新公告**（入站 ann_date > 既有 ann_date）→ 正常修订
  （updated，按来源最新公告更新）；
- 值不同且**非新公告**（入站 ann_date ≤ 既有 ann_date）→ RECORD_CONFLICT
  整批失败，不得任意覆盖；
- 值相同 → unchanged；无既有行 → added。

水位（增量推进依据）按接口/kind 分别计算（research 决策 1）：
``TOP10_HOLDERS``/``TOP10_FLOAT_HOLDERS`` 各取 ``shareholder_holding``
对应 ``holder_kind`` 的 ``max(ann_date) FINAL``，``HOLDER_COUNT`` 取
``shareholder_count`` 的 ``max(ann_date) FINAL``——两 top10 接口同表
写入，表级水位会让先运行的接口把后运行接口的当日公告一并跳过。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from lucking.clickhouse import ClickHouseClient
from lucking.models.shareholder_data import (
    ShareholderCount,
    ShareholderHolding,
)
from lucking.repositories.market_data import MarketDataValidationError

SHAREHOLDER_HOLDING_TABLE = "shareholder_holding"
SHAREHOLDER_COUNT_TABLE = "shareholder_count"

# 持仓表数据列（身份列之后、updated_at 之前；holder_kind/holder_name 是
# 业务身份组成部分，不参与值比较；ann_date 为修订锚点）。
_HOLDING_DATA_COLUMNS = (
    "hold_amount",
    "hold_ratio",
    "hold_float_ratio",
    "hold_change",
    "holder_type",
)
_HOLDING_COLUMNS = (
    "end_date",
    "stock_id",
    "holder_kind",
    "holder_name",
    "ann_date",
    "stock_code",
    *_HOLDING_DATA_COLUMNS,
    "updated_at",
)
_COUNT_COLUMNS = (
    "end_date",
    "stock_id",
    "ann_date",
    "stock_code",
    "holder_num",
    "updated_at",
)


class ShareholderDataClickHouseRepository:
    def __init__(self, client: ClickHouseClient) -> None:
        self._client = client

    # ---- 水位（增量推进依据，按接口/kind）----

    def top10_holders_watermark(self) -> date | None:
        return self._max_ann_date(SHAREHOLDER_HOLDING_TABLE, holder_kind="TOP10")

    def top10_float_holders_watermark(self) -> date | None:
        return self._max_ann_date(SHAREHOLDER_HOLDING_TABLE, holder_kind="TOP10_FLOAT")

    def holder_count_watermark(self) -> date | None:
        return self._max_ann_date(SHAREHOLDER_COUNT_TABLE)

    def _max_ann_date(self, table: str, *, holder_kind: str | None = None) -> date | None:
        where = f" WHERE holder_kind = '{holder_kind}'" if holder_kind else ""
        rows = self._client.execute(
            f"SELECT max(ann_date) AS watermark FROM {self._client.database}.{table} "
            f"FINAL{where}"
        )
        value = rows[0]["watermark"] if rows else None
        if value is None:
            return None
        if isinstance(value, str):
            return date.fromisoformat(value)
        if isinstance(value, datetime):
            return value.date()
        return value  # type: ignore[no-any-return]  # ClickHouse Date 恒为 str/datetime

    # ---- 发布 ----

    def publish_holdings(
        self,
        records: Sequence[ShareholderHolding],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        """单 block 写入全部持仓候选，返回 (added, updated, unchanged)。"""
        if not records:
            return 0, 0, 0
        rows = [_holding_row(record, updated_at) for record in records]
        existing = self._existing_holdings(records)
        counts = _compare_holdings(rows, existing)
        self._client.insert_rows(SHAREHOLDER_HOLDING_TABLE, _HOLDING_COLUMNS, rows)
        return counts

    def publish_counts(
        self,
        records: Sequence[ShareholderCount],
        updated_at: datetime,
    ) -> tuple[int, int, int]:
        """单 block 写入全部股东人数候选，返回 (added, updated, unchanged)。"""
        if not records:
            return 0, 0, 0
        rows = [_count_row(record, updated_at) for record in records]
        existing = self._existing_counts(records)
        counts = _compare_counts(rows, existing)
        self._client.insert_rows(SHAREHOLDER_COUNT_TABLE, _COUNT_COLUMNS, rows)
        return counts

    def _existing_holdings(
        self, records: Sequence[ShareholderHolding]
    ) -> dict[tuple[str, str, str, str], dict[str, Any]]:
        """读取同键既有行（FINAL 去重），供修订/冲突判定。"""
        keys: set[tuple[str, str, str, str]] = set()
        for record in records:
            keys.add(
                (
                    record.end_date.isoformat(),
                    record.stock_id,
                    record.holder_kind,
                    record.holder_name,
                )
            )
        clauses = " OR ".join(
            f"(end_date = '{end}' AND stock_id = '{stock}' "
            f"AND holder_kind = '{kind}' AND holder_name = '{_quote(name)}')"
            for end, stock, kind, name in keys
        )
        if not clauses:
            return {}
        selected = ", ".join(_HOLDING_COLUMNS)
        rows = self._client.execute(
            f"SELECT {selected} FROM {self._client.database}.{SHAREHOLDER_HOLDING_TABLE} FINAL "
            f"WHERE {clauses}"
        )
        result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                _to_iso(row["end_date"]),
                _clean_id(row["stock_id"]),
                _kind_value(row["holder_kind"]),
                str(row["holder_name"]),
            )
            result[key] = {column: _normalize_value(row[column]) for column in _HOLDING_COLUMNS}
        return result

    def _existing_counts(
        self, records: Sequence[ShareholderCount]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        keys: set[tuple[str, str]] = {
            (record.end_date.isoformat(), record.stock_id) for record in records
        }
        clauses = " OR ".join(
            f"(end_date = '{end}' AND stock_id = '{stock}')" for end, stock in keys
        )
        if not clauses:
            return {}
        selected = ", ".join(_COUNT_COLUMNS)
        rows = self._client.execute(
            f"SELECT {selected} FROM {self._client.database}.{SHAREHOLDER_COUNT_TABLE} FINAL "
            f"WHERE {clauses}"
        )
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (_to_iso(row["end_date"]), _clean_id(row["stock_id"]))
            result[key] = {column: _normalize_value(row[column]) for column in _COUNT_COLUMNS}
        return result

    # ---- 内部查询（消费契约）----

    def query_shareholder_holdings(
        self,
        *,
        stock_id: str | None = None,
        holder_kind: str | None = None,
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
        if holder_kind is not None:
            clauses.append(f"holder_kind = '{holder_kind}'")
        if start_date is not None:
            clauses.append(f"end_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"end_date <= '{end_date.isoformat()}'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._client.execute(
            f"SELECT * FROM {self._client.database}.{SHAREHOLDER_HOLDING_TABLE} FINAL{where} "
            f"ORDER BY end_date, stock_id, holder_kind, holder_name LIMIT {limit} OFFSET {offset}"
        )
        return tuple(_clean_row(row) for row in rows)

    def query_shareholder_count(
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
            clauses.append(f"end_date >= '{start_date.isoformat()}'")
        if end_date is not None:
            clauses.append(f"end_date <= '{end_date.isoformat()}'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._client.execute(
            f"SELECT * FROM {self._client.database}.{SHAREHOLDER_COUNT_TABLE} FINAL{where} "
            f"ORDER BY end_date, stock_id LIMIT {limit} OFFSET {offset}"
        )
        return tuple(_clean_row(row) for row in rows)


def _holding_row(record: ShareholderHolding, updated_at: datetime) -> dict[str, Any]:
    return {
        "end_date": record.end_date,
        "stock_id": record.stock_id,
        "holder_kind": record.holder_kind,
        "holder_name": record.holder_name,
        "ann_date": record.ann_date,
        "stock_code": record.stock_code,
        "hold_amount": record.hold_amount,
        "hold_ratio": record.hold_ratio,
        "hold_float_ratio": record.hold_float_ratio,
        "hold_change": record.hold_change,
        "holder_type": record.holder_type,
        "updated_at": updated_at,
    }


def _count_row(record: ShareholderCount, updated_at: datetime) -> dict[str, Any]:
    return {
        "end_date": record.end_date,
        "stock_id": record.stock_id,
        "ann_date": record.ann_date,
        "stock_code": record.stock_code,
        "holder_num": record.holder_num,
        "updated_at": updated_at,
    }


def _compare_holdings(
    rows: Sequence[Mapping[str, Any]],
    existing: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> tuple[int, int, int]:
    added = updated = unchanged = 0
    for row in rows:
        key = (
            _to_iso(row["end_date"]),
            _clean_id(row["stock_id"]),
            _kind_value(row["holder_kind"]),
            str(row["holder_name"]),
        )
        previous = existing.get(key)
        if previous is None:
            added += 1
            continue
        classification = _classify(row, previous, _HOLDING_DATA_COLUMNS)
        if classification == "updated":
            updated += 1
        else:
            unchanged += 1
    return added, updated, unchanged


def _compare_counts(
    rows: Sequence[Mapping[str, Any]],
    existing: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[int, int, int]:
    added = updated = unchanged = 0
    for row in rows:
        key = (_to_iso(row["end_date"]), _clean_id(row["stock_id"]))
        previous = existing.get(key)
        if previous is None:
            added += 1
            continue
        classification = _classify(row, previous, ("holder_num",))
        if classification == "updated":
            updated += 1
        else:
            unchanged += 1
    return added, updated, unchanged


def _classify(
    row: Mapping[str, Any],
    previous: Mapping[str, Any],
    data_columns: tuple[str, ...],
) -> str:
    """按 ann_date 锚点判定修订 vs 冲突（spec FR-010/ED-010）。"""
    incoming_ann = _normalize_value(row["ann_date"])
    existing_ann = previous.get("ann_date")
    value_differs = any(
        _normalize_value(row[column]) != previous.get(column) for column in data_columns
    )
    if not value_differs:
        return "unchanged"  # ann_date 元数据随 ReplacingMergeTree 自然更新
    if incoming_ann is not None and existing_ann is not None and incoming_ann > existing_ann:
        return "updated"  # 新公告（更正公告）→ 按最新公告值更新
    raise MarketDataValidationError("RECORD_CONFLICT", "同一业务身份存在非新公告的字段冲突")


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


def _to_iso(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def _kind_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _quote(name: str) -> str:
    return name.replace("'", "\\'")
