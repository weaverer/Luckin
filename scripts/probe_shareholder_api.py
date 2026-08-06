"""探针：验证前十大股东 / 前十大流通股东 / 股东人数三个接口的真实返回。

按用户要求先以 limit=1 探测"昨天"（ann_date/公告日期）的数据，
若无数据则回退到近期报告期/公告区间查看真实字段与样例行。
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


def call(api_name: str, params: dict[str, object], label: str) -> None:
    payload: dict[str, object] = {"api_name": api_name, "token": _TOKEN, "params": params}
    resp = httpx.post(API_URL, json=payload, timeout=60)
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
    print(f"fields({len(fields)}): {fields}")
    for row in rows[:3]:
        print("row:", dict(zip(fields, row, strict=True)))
    print()


# 1. 前十大股东：昨天公告 + 近期报告期
call("top10_holders", {"ts_code": "600000.SH", "ann_date": "20260804", "limit": 1},
     "top10_holders 昨天公告 ann_date=20260804 limit=1")
call("top10_holders", {"ts_code": "600000.SH", "start_date": "20260331", "end_date": "20260804", "limit": 1},
     "top10_holders 近期报告期 limit=1")
call("top10_holders", {"ts_code": "600000.SH", "start_date": "20260331", "end_date": "20260804"},
     "top10_holders 近期报告期（无 limit，看全量行为）")

# 2. 前十大流通股东
call("top10_floatholders", {"ts_code": "600000.SH", "ann_date": "20260804", "limit": 1},
     "top10_floatholders 昨天公告 ann_date=20260804 limit=1")
call("top10_floatholders", {"ts_code": "600000.SH", "start_date": "20260331", "end_date": "20260804", "limit": 1},
     "top10_floatholders 近期报告期 limit=1")

# 3. 股东人数：昨天公告 + 近期公告区间
call("stk_holdernumber", {"ts_code": "300199.SZ", "start_date": "20260801", "end_date": "20260804", "limit": 1},
     "stk_holdernumber 昨天公告区间 limit=1")
call("stk_holdernumber", {"ts_code": "300199.SZ", "start_date": "20260401", "end_date": "20260804", "limit": 1},
     "stk_holdernumber 近期公告区间 limit=1")
