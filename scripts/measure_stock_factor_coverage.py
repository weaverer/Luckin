"""部署前待验证项 5：003 股票映射对 stk_factor_pro ts_code 全集的覆盖度实测。

一次 API 调用 + MySQL 只读查询；结果供 research.md 待验证项 5 回填。
"""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

from lucking.config import Settings

_TOKEN = None
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line.startswith("TUSHARE_TOKEN=") and not line.startswith("#"):
        _TOKEN = line.split("=", 1)[1].strip().strip('"')
        break
assert _TOKEN, "TUSHARE_TOKEN 未配置"


def main() -> None:
    resp = httpx.post(
        "https://api.tushare.pro",
        json={
            "api_name": "stk_factor_pro",
            "token": _TOKEN,
            "params": {"trade_date": "20260803"},
            "fields": "ts_code,trade_date",
        },
        timeout=60,
    )
    data = resp.json()
    assert data.get("code") == 0, data.get("msg")
    codes = {row[0] for row in data["data"]["items"]}
    print("API ts_codes:", len(codes))

    engine = create_engine(Settings().database_url)
    try:
        with engine.connect() as conn:
            mapped = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT provider_security_id FROM stock_provider_mapping "
                        "WHERE provider_code = :code"
                    ).bindparams(code="tushare")
                )
            }
    finally:
        engine.dispose()
    print("003 mapped ts_codes:", len(mapped))
    uncovered = codes - mapped
    print("UNCOVERED:", len(uncovered))
    print("sample uncovered:", sorted(uncovered)[:8])
    suffixes: dict[str, int] = {}
    for code in codes:
        suffix = code[-3:]
        suffixes[suffix] = suffixes.get(suffix, 0) + 1
    print("suffix distribution:", suffixes)


if __name__ == "__main__":
    main()
