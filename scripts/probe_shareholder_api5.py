"""探针 5：验证显式 fields 请求与 has_more 标志（Adapter 实现的最后前提）。

TushareClient 固定发送 fields 参数并要求响应字段逐名一致；
探针确认三个股东接口在显式 fields 下返回一致，且 has_more 键存在。
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
TOP10_FIELDS = ["ts_code", "ann_date", "end_date", "holder_name",
                "hold_amount", "hold_ratio", "hold_float_ratio",
                "hold_change", "holder_type"]
COUNT_FIELDS = ["ts_code", "ann_date", "end_date", "holder_num"]


def call(api_name: str, params: dict[str, object], fields: list[str], label: str) -> None:
    payload = {"api_name": api_name, "token": _TOKEN, "params": params, "fields": ",".join(fields)}
    resp = httpx.post(API_URL, json=payload, timeout=120)
    data = resp.json()
    print(f"== {label} ==")
    print(f"params: {params} fields: {len(fields)}")
    if data.get("code") != 0:
        print(f"API ERROR code={data.get('code')} msg={data.get('msg')}")
        print()
        return
    body = data["data"]
    returned = body["fields"]
    print(f"code=0 rows={len(body['items'])} has_more={body.get('has_more')} "
          f"返回字段一致={set(returned) == set(fields)}")
    if body["items"]:
        print("row:", dict(zip(returned, body["items"][0], strict=True)))
    print()


# 1. top10_holders 显式 fields + limit=1
call("top10_holders", {"ann_date": "20260430", "limit": 1}, TOP10_FIELDS,
     "top10_holders 显式 fields")
# 2. top10_floatholders 显式 fields + limit=1
call("top10_floatholders", {"ann_date": "20260430", "limit": 1}, TOP10_FIELDS,
     "top10_floatholders 显式 fields")
# 3. stk_holdernumber 显式 fields + limit=1
call("stk_holdernumber", {"start_date": "20260429", "end_date": "20260429", "limit": 1},
     COUNT_FIELDS, "stk_holdernumber 显式 fields")
