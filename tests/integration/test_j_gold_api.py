import json
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from lucking.api.dependencies import get_current_session
from lucking.api.main import create_app
from lucking.api.routes import j_gold
from lucking.config import Settings
from lucking.models.j_gold import QuotePoint, RecommendationFact
from lucking.services.auth import AuthenticatedSession
from lucking.services.j_gold_research import JGoldResearchService


class Reader:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.now = now
        self.facts = [
            RecommendationFact(
                date(2026, 8, 1),
                "研究券商",
                "stock-1",
                "600000",
                "研究样本",
                "ACTIVE",
                None,
                now,
            )
        ]

    def available_months(self) -> list[date]:
        return [date(2026, 8, 1)]

    def recommendations(self, start_month: date, end_month: date) -> list[RecommendationFact]:
        return self.facts

    def stock_quotes(
        self, stock_id: str, limit: int = 80, start_date: date | None = None
    ) -> list[QuotePoint]:
        return [
            QuotePoint(date(2026, 4, 1) + timedelta(days=index), 10 + index / 10, self.now)
            for index in range(limit)
        ]

    def stock_quotes_batch(
        self, stock_ids: list[str], limit: int = 400
    ) -> dict[str, list[QuotePoint]]:
        return {stock_id: self.stock_quotes(stock_id, limit) for stock_id in stock_ids}

    def benchmark_quotes(self, limit: int = 80, start_date: date | None = None) -> list[QuotePoint]:
        return [
            QuotePoint(date(2026, 4, 1) + timedelta(days=index), 10 + index / 20, self.now)
            for index in range(limit)
        ]


def test_j_gold_api_returns_unified_envelope_and_traceable_detail(tmp_path, monkeypatch) -> None:
    app = create_app(Settings(trading_calendar_log_dir=tmp_path))
    app.dependency_overrides[get_current_session] = lambda: AuthenticatedSession(
        user_id="user-1",
        username="researcher",
        display_name="研究员",
        session_token="session",
        csrf_token="csrf-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(j_gold, "service", lambda: JGoldResearchService(Reader()))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/j-gold/research",
            params={"recommendation_month": "2026-08-01"},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"code", "message", "data", "errors", "request_id", "timestamp"}
        assert body["code"] == 0
        assert response.headers["X-Request-ID"] == body["request_id"]
        assert body["data"]["pagination"]["total"] == 1
        assert body["data"]["quality"]["status"] == "partial"

        detail = client.get(
            "/api/v1/j-gold/stocks/stock-1",
            params={"recommendation_month": "2026-08-01"},
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["price_basis"].startswith("后复权")

    records = [
        json.loads(line)
        for line in (tmp_path / "workbench-api.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    research_log = next(item for item in records if item["path"] == "/api/v1/j-gold/research")
    assert research_log["request_id"] == body["request_id"]
    assert research_log["duration_ms"] >= 0
    assert "研究样本" not in json.dumps(research_log, ensure_ascii=False)
