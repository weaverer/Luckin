"""开发辅助：以项目 Settings 的 database_url 作为 TEST_DATABASE_URL 运行
008 股东数据集成测试（真实 MySQL + ClickHouse）。

用法：.venv/bin/python scripts/run_shareholder_integration.py [pytest 参数]
"""

from __future__ import annotations

import os
import sys

from lucking.config import Settings

os.environ["TEST_DATABASE_URL"] = Settings().database_url

import pytest  # noqa: E402

TARGETS = [
    "tests/integration/test_shareholder_data_schema.py",
    "tests/integration/test_shareholder_data_sync.py",
    "tests/integration/test_shareholder_data_backfill.py",
    "tests/integration/test_shareholder_data_observability.py",
]
sys.exit(pytest.main([*TARGETS, *sys.argv[1:]]))
