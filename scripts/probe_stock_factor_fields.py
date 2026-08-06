"""探针：对比 stk_factor_pro 显式 fields 请求与无 fields 请求的响应字段集。

用于诊断"响应字段与请求不一致"（字段集合不精确 → ProviderPayloadError）。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from lucking.integrations.tushare.stock_factor_provider import PROVIDER_STOCK_FACTOR_FIELDS

_TOKEN = None
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
        _TOKEN = line.split("=", 1)[1].strip().strip('"')
        break
assert _TOKEN, "TUSHARE_TOKEN 未配置"

REQUESTED = set(PROVIDER_STOCK_FACTOR_FIELDS)


def _call(fields: str | None, label: str) -> set[str]:
    payload: dict[str, object] = {
        "api_name": "stk_factor_pro",
        "token": _TOKEN,
        "params": {"trade_date": "20260803"},
    }
    if fields:
        payload["fields"] = fields
    resp = httpx.post("https://api.tushare.pro", json=payload, timeout=60)
    data = resp.json()
    if data.get("code") != 0:
        print(f"{label}: API ERROR {data.get('code')} {data.get('msg')}")
        return set()
    returned = set(data["data"]["fields"])
    print(f"{label}: returned {len(returned)} fields")
    if returned != REQUESTED:
        print(f"  only-in-response: {sorted(returned - REQUESTED)[:10]}")
        print(f"  only-in-request : {sorted(REQUESTED - returned)[:10]}")
    else:
        print("  == 与白名单完全一致")
    return returned


def main() -> None:
    print(f"白名单（请求字段）: {len(REQUESTED)}")
    _call(",".join(PROVIDER_STOCK_FACTOR_FIELDS), "显式 fields")
    _call(None, "无 fields（全字段）")


if __name__ == "__main__":
    main()
