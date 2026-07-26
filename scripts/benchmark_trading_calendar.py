"""可复现的交易日历数据库性能基准。"""

from __future__ import annotations

import json
import platform
import statistics
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete

from lucking.config import Settings
from lucking.db import create_database_engine, create_session_factory
from lucking.models.trading_calendar import TradingCalendar
from lucking.ports.trading_calendar_provider import MarketCode, ProviderCalendarDay, SyncMode
from lucking.repositories.trading_calendar import SqlAlchemyTradingCalendarRepository


def percentile_95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[94]


def main() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    repository = SqlAlchemyTradingCalendarRepository(factory)
    start = date(2080, 1, 1)
    end = date(2089, 12, 31)
    rows = [
        ProviderCalendarDay(
            MarketCode.CN_STOCK,
            start + timedelta(days=offset),
            (start + timedelta(days=offset)).weekday() < 5,
            None,
            "benchmark",
            "BENCHMARK",
        )
        for offset in range((end - start).days + 1)
    ]
    sync_samples: list[float] = []
    query_samples: list[float] = []
    try:
        for _ in range(20):
            before = time.perf_counter()
            repository.upsert_batch(rows, SyncMode.MANUAL, datetime.now(UTC))
            sync_samples.append((time.perf_counter() - before) * 1000)
        for _ in range(500):
            before = time.perf_counter()
            repository.get("CN-S", date(2085, 6, 15))
            query_samples.append((time.perf_counter() - before) * 1000)
    finally:
        with factory.begin() as session:
            session.execute(
                delete(TradingCalendar).where(
                    TradingCalendar.market_code == "CN-S",
                    TradingCalendar.calendar_date >= start,
                    TradingCalendar.calendar_date <= end,
                    TradingCalendar.source == "benchmark",
                )
            )
        engine.dispose()

    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "database_dialect": engine.dialect.name,
                "range_days": len(rows),
                "sync_sample_size": len(sync_samples),
                "sync_p95_ms": round(percentile_95(sync_samples), 3),
                "query_sample_size": len(query_samples),
                "query_p95_ms": round(percentile_95(query_samples), 3),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

