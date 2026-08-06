"""探针 4：确认 has_more 分页机制（offset 参数）。

响应含 has_more: True 时如何取下一页？验证 offset 参数是否生效，
确定触顶判定与续取策略（spec FR-006/ED-003/ED-008 完整性门禁的依据）。
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
    extra = {k: v for k, v in body.items() if k not in ("fields", "items")}
    print(f"code=0 rows={len(body['items'])} 附加键: {extra}")
    if body["items"]:
        print("首行:", dict(zip(body["fields"], body["items"][0], strict=True)))
    print()


# 1. top10_holders offset 翻页（报告期 20260331 共 ~54k 行，每页 6000）
call("top10_holders", {"start_date": "20260331", "end_date": "20260331", "offset": 6000, "limit": 6000},
     "top10_holders offset=6000 报告期=20260331")
call("top10_holders", {"start_date": "20260331", "end_date": "20260331", "offset": 12000, "limit": 6000},
     "top10_holders offset=12000 报告期=20260331")

# 2. stk_holdernumber 三日窗口是否触顶 + offset 翻页
call("stk_holdernumber", {"start_date": "20260428", "end_date": "20260430"},
     "stk_holdernumber 3日窗口（查 has_more）")
call("stk_holdernumber", {"start_date": "20260428", "end_date": "20260430", "offset": 6000, "limit": 6000},
     "stk_holdernumber 3日窗口 offset=6000")
