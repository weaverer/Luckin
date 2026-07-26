from dataclasses import fields
from pathlib import Path

from lucking.models.stock_list import StockCurrent
from lucking.ports.stock_list_provider import StockListRequest


def test_public_request_has_no_venue_subset_or_provider_fields() -> None:
    assert [field.name for field in fields(StockListRequest)] == ["scope_code"]
    assert "venue_codes" not in {field.name for field in fields(StockListRequest)}


def test_adapter_source_contains_only_authorized_endpoint_and_fields() -> None:
    source = Path(
        "src/lucking/integrations/tushare/stock_list_provider.py"
    ).read_text()
    assert source.count('"stock_basic"') == 1
    for endpoint in (
        '"trade_cal"',
        '"daily"',
        '"daily_basic"',
        '"income"',
        '"balancesheet"',
        '"cashflow"',
        '"stock_company"',
    ):
        assert endpoint not in source


def test_persistence_model_has_no_forbidden_stock_basic_fields() -> None:
    columns = set(StockCurrent.__table__.columns.keys())
    assert not columns.intersection(
        {
            "ts_code",
            "area",
            "industry",
            "fullname",
            "enname",
            "cnspell",
            "market",
            "is_hs",
            "act_name",
            "act_ent_type",
        }
    )

