import pytest
from pydantic import ValidationError

from lucking.config import Settings


def test_broker_recommendation_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)
    assert settings.broker_recommendation_timezone == "Asia/Shanghai"
    assert settings.broker_recommendation_fetch_deadline_seconds == 1500
    assert settings.broker_recommendation_run_lease_seconds == 2100
    assert settings.broker_recommendation_page_limit == 1000
    assert settings.broker_recommendation_backfill_max_months == 120
    assert not settings.broker_recommendation_tushare_pagination_enabled


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BROKER_RECOMMENDATION_PAGE_LIMIT", "999"),
        ("BROKER_RECOMMENDATION_RUN_LEASE_SECONDS", "2099"),
        ("BROKER_RECOMMENDATION_BACKFILL_MAX_MONTHS", "121"),
    ],
)
def test_fixed_safety_settings_reject_overrides(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_token_is_validated_only_when_adapter_requests_it() -> None:
    settings = Settings(_env_file=None, tushare_token=None)
    with pytest.raises(ValueError):
        settings.require_tushare_token()
