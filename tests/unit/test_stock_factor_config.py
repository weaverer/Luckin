"""Stock-factor 配置默认值与安全不变量（T001 配套单元测试）。"""

import pytest
from pydantic import ValidationError

from lucking.config import Settings


def test_stock_factor_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.stock_factor_provider == "tushare"
    assert settings.stock_factor_timezone == "Asia/Shanghai"
    assert settings.stock_factor_log_filename == "stock-factor-sync.jsonl"
    assert settings.stock_factor_fetch_deadline_seconds == 1500
    assert settings.stock_factor_run_lease_seconds == 2100
    assert settings.stock_factor_page_limit == 10000
    assert settings.stock_factor_rate_limit_per_minute == 30


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STOCK_FACTOR_PAGE_LIMIT", "9999"),
        ("STOCK_FACTOR_RUN_LEASE_SECONDS", "2099"),
        ("STOCK_FACTOR_RATE_LIMIT_PER_MINUTE", "29"),
    ],
)
def test_fixed_safety_settings_reject_overrides(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_lease_must_exceed_fetch_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCK_FACTOR_FETCH_DEADLINE_SECONDS", "2500")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_provider_selection_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCK_FACTOR_PROVIDER", " Tushare ")
    settings = Settings(_env_file=None)
    assert settings.stock_factor_provider == "tushare"
