"""探针 3：实测全市场查询的行数量级与真实单次返回上限。

决定：按公告日/报告期提取时是否触顶（触顶即不完整），以及是否需要分页。
检查响应体是否含 has_more 等完整性标记。
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
    resp = httpx.post(API_URL, json=payload, timeout=180)
    data = resp.json()
    print(f"== {label} ==")
    print(f"params: {params}")
    if data.get("code") != 0:
        print(f"API ERROR code={data.get('code')} msg={data.get('msg')}")
        print()
        return
    body = data["data"]
    print(f"code=0 rows={len(body['items'])}")
    extra = {k: v for k, v in body.items() if k not in ("fields", "items")}
    print(f"data 附加键: {extra}")
    if body["items"]:
        print("row:", dict(zip(body["fields"], body["items"][0], strict=True)))
    print()


# 1. top10_holders 全市场单报告期（Q1 2026 全量行数，预期 ~5.4k 股票 x 10 股东）
call("top10_holders", {"start_date": "20260331", "end_date": "20260331"},
     "top10_holders 全市场 报告期=20260331（无 limit）")

# 2. top10_holders 全市场单公告日（20260430 披露高峰）
call("top10_holders", {"ann_date": "20260430"},
     "top10_holders 全市场 ann_date=20260430（无 limit）")

# 3. top10_floatholders 全市场单报告期
call("top10_floatholders", {"start_date": "20260331", "end_date": "20260331"},
     "top10_floatholders 全市场 报告期=20260331（无 limit）")

# 4. stk_holdernumber 全市场单公告日（披露高峰日）
call("stk_holdernumber", {"start_date": "20260429", "end_date": "20260429"},
     "stk_holdernumber 全市场 ann=20260429（无 limit）")
