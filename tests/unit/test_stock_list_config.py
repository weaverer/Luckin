from pathlib import Path

import pytest
from pydantic import ValidationError

from lucking.config import Settings


def test_stock_list_defaults_are_fixed_and_have_no_venue_subset() -> None:
    settings = Settings(_env_file=None)
    assert settings.stock_list_provider == "tushare"
    assert settings.stock_list_scope == "CN-S"
    assert settings.stock_list_timezone == "Asia/Shanghai"
    assert settings.stock_list_log_dir == Path("logs")
    assert settings.stock_list_log_filename == "stock-list-sync.jsonl"
    assert settings.stock_list_fetch_deadline_seconds == 1500
    assert settings.stock_list_timeliness_target_ms == 1_800_000
    assert settings.stock_list_segment_row_cap == 6000
    assert "stock_list_venue" not in type(settings).model_fields


def test_stock_list_rejects_unknown_scope_and_provider() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stock_list_scope="US")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stock_list_provider="")


def test_tushare_token_is_only_required_when_adapter_is_built() -> None:
    settings = Settings(_env_file=None, tushare_token=None)
    assert settings.stock_list_provider == "tushare"
    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        settings.require_tushare_token()

