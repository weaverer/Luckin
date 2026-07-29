from datetime import date
from pathlib import Path

import pytest

from lucking.flows.broker_recommendation import expand_month_range


def test_month_range_accepts_120_and_rejects_121_before_work() -> None:
    accepted = expand_month_range(
        date(2016, 8, 1),
        date(2026, 7, 1),
        today=date(2026, 7, 28),
    )
    assert len(accepted) == 120
    with pytest.raises(ValueError):
        expand_month_range(
            date(2016, 7, 1),
            date(2026, 7, 1),
            today=date(2026, 7, 28),
        )


def test_initialization_backfill_expands_24_inclusive_months() -> None:
    months = expand_month_range(
        date(2024, 8, 1),
        date(2026, 7, 1),
        today=date(2026, 7, 28),
    )
    assert len(months) == 24
    assert months[0] == date(2024, 8, 1)
    assert months[-1] == date(2026, 7, 1)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2026, 7, 2), date(2026, 7, 1)),
        (date(2026, 8, 1), date(2026, 8, 1)),
        (date(2026, 7, 1), date(2026, 6, 1)),
    ],
)
def test_invalid_month_ranges_fail(start: date, end: date) -> None:
    with pytest.raises(ValueError):
        expand_month_range(start, end, today=date(2026, 7, 28))


def test_prefect_schedule_is_exact() -> None:
    yaml = Path("prefect.yaml").read_text()
    assert 'cron: "0 12 3,4 * *"' in yaml
    assert "timezone: Asia/Shanghai" in yaml
    assert "monthly-broker-recommendations" in yaml
    assert "collision_strategy: ENQUEUE" in yaml
