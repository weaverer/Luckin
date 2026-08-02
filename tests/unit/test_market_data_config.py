"""Market-data configuration defaults and safety invariants."""

import pytest
from pydantic import ValidationError

from lucking.config import Settings


def test_market_data_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.daily_quote_provider == "tushare"
    assert settings.adj_factor_provider == "tushare"
    assert settings.daily_basic_provider == "tushare"
    assert settings.kline_provider == "tushare"
    assert settings.market_data_timezone == "Asia/Shanghai"
    assert settings.market_data_log_filename == "market-data-sync.jsonl"
    assert settings.market_data_fetch_deadline_seconds == 1500
    assert settings.market_data_run_lease_seconds == 2100
    assert settings.market_data_page_limit == 6000
    assert settings.market_data_max_pages == 10
    assert not settings.market_data_tushare_pagination_enabled
    assert settings.clickhouse_host == "127.0.0.1"
    assert settings.clickhouse_port == 8123
    assert settings.clickhouse_database == "lucking"
    assert settings.clickhouse_user == "lucking"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MARKET_DATA_PAGE_LIMIT", "5999"),
        ("MARKET_DATA_RUN_LEASE_SECONDS", "2099"),
        ("MARKET_DATA_MAX_PAGES", "0"),
    ],
)
def test_fixed_safety_settings_reject_overrides(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_lease_must_exceed_fetch_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_DATA_FETCH_DEADLINE_SECONDS", "2500")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_provider_selection_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KLINE_PROVIDER", " Tushare ")
    settings = Settings(_env_file=None)
    assert settings.kline_provider == "tushare"


def test_clickhouse_port_range() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, clickhouse_port=70000)


def test_clickhouse_password_is_lazy_secret() -> None:
    settings = Settings(_env_file=None, clickhouse_password="local-secret")
    assert settings.clickhouse_password is not None
    assert settings.clickhouse_password.get_secret_value() == "local-secret"
