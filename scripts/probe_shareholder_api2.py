"""探针 2：确认前十大股东/流通股东/股东人数的提取模式。

关键设计问题：接口是否支持不带 ts_code 的全市场查询（按公告日/报告期），
还是必须按股票逐只调用？这决定增量同步的请求量与分页策略。
"""

from __future__ import annotations

from pathlib import Path

import httpx

_TOKEN = None
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
        _TOKEN = line.split("=", 1)[1].strip().strip('"')
        break
assert _TOKEN, "TUSHARE_TOKEN 未配置"

API_URL = "https://api.tushare.pro"


def call(
    api_name: str, params: dict[str, object], label: str, limit_rows: int | None = None
) -> None:
    payload: dict[str, object] = {"api_name": api_name, "token": _TOKEN, "params": params}
    resp = httpx.post(API_URL, json=payload, timeout=120)
    data = resp.json()
    print(f"== {label} ==")
    print(f"params: {params}")
    if data.get("code") != 0:
        print(f"API ERROR code={data.get('code')} msg={data.get('msg')}")
        print()
        return
    body = data["data"]
    fields = body["fields"]
    rows = body["items"]
    print(f"code=0 rows={len(rows)}")
    if limit_rows is not None:
        for row in rows[:limit_rows]:
            print("row:", dict(zip(fields, row, strict=True)))
    print()


# 1. top10_holders 不带 ts_code，按公告日查询（全市场？）
call(
    "top10_holders",
    {"ann_date": "20260430", "limit": 1},
    "top10_holders 无ts_code ann_date=20260430 limit=1",
)

# 2. top10_holders 带 ts_code + ann_date（按股票按公告日）
call(
    "top10_holders",
    {"ts_code": "600000.SH", "ann_date": "20260430", "limit": 1},
    "top10_holders ts_code+ann_date=20260430 limit=1",
)

# 3. top10_holders 不带 ts_code，按报告期查询（全市场？）
call(
    "top10_holders",
    {"start_date": "20260331", "end_date": "20260331", "limit": 1},
    "top10_holders 无ts_code 报告期=20260331 limit=1",
)

# 4. top10_floatholders 不带 ts_code，按公告日查询（全市场？）
call(
    "top10_floatholders",
    {"ann_date": "20260430", "limit": 1},
    "top10_floatholders 无ts_code ann_date=20260430 limit=1",
)

# 5. stk_holdernumber 不带 ts_code，按公告区间（全市场？）
call(
    "stk_holdernumber",
    {"start_date": "20260401", "end_date": "20260430", "limit": 1},
    "stk_holdernumber 无ts_code 公告区间 limit=1",
)

# 6. stk_holdernumber 无 ts_code 单日公告量级（不分页看行数，为分页/上限定参）
call(
    "stk_holdernumber",
    {"start_date": "20260428", "end_date": "20260430"},
    "stk_holdernumber 无ts_code 公告区间（全量行数）",
)
