from lucking.api.main import create_app
from lucking.config import Settings


def test_j_gold_openapi_is_typed_and_uses_unified_responses(tmp_path) -> None:
    app = create_app(Settings(trading_calendar_log_dir=tmp_path))
    schema = app.openapi()
    research = schema["paths"]["/api/v1/j-gold/research"]["get"]
    detail = schema["paths"]["/api/v1/j-gold/stocks/{stock_id}"]["get"]
    assert research["operationId"] == "getJGoldResearch"
    radar_filter = next(
        parameter for parameter in research["parameters"] if parameter["name"] == "radar_filter"
    )
    assert set(radar_filter["schema"]["anyOf"][0]["enum"]) == {
        "monthly",
        "new",
        "consensus",
        "warming",
        "breakout",
        "excess",
    }
    assert set(research["responses"]) >= {"200", "400", "401", "500"}
    assert set(detail["responses"]) >= {"200", "400", "401", "404", "500"}
    components = schema["components"]["schemas"]
    assert "ResearchDataDto" in components
    assert "RadarItemDto" in components
    response = components["ApiResponse_ResearchDataDto_"]
    assert set(response["required"]) == {
        "code",
        "message",
        "data",
        "errors",
        "request_id",
        "timestamp",
    }
