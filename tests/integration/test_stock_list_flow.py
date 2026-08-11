from datetime import UTC, datetime
from pathlib import Path

import yaml

from lucking.flows.stock_list import sync_stock_list
from lucking.models.stock_list import SyncStatus
from lucking.services.stock_list import StockListSyncResult


def test_prefect_yaml_has_daily_stock_list_deployment_without_venue_params() -> None:
    config = yaml.safe_load(Path("prefect.yaml").read_text())
    deployment = next(
        item
        for item in config["deployments"]
        if item["entrypoint"] == "src/lucking/flows/stock_list.py:sync_stock_list"
    )
    assert deployment["name"] == "股票列表同步"
    assert deployment["entrypoint"] == "src/lucking/flows/stock_list.py:sync_stock_list"
    schedule = deployment["schedules"][0]
    assert schedule["cron"] == "0 9 * * *"
    assert schedule["timezone"] == "Asia/Shanghai"
    assert schedule["slug"] == "daily-stock-list"
    assert schedule["parameters"] == {
        "scope_code": "CN-S",
        "schedule_slug": "daily-stock-list",
    }


def test_flow_composes_service_and_returns_success(monkeypatch, tmp_path) -> None:
    class Service:
        def sync(self, command):
            assert command.scope_code.value == "CN-S"
            return StockListSyncResult(
                "run-1",
                "key",
                SyncStatus.SUCCEEDED,
                1,
                command.scheduled_at.astimezone().date(),
                "memory",
                1,
                1,
                0,
                0,
                0,
                1,
                0,
                0,
            )

    monkeypatch.setenv("STOCK_LIST_LOG_DIR", str(tmp_path))
    monkeypatch.setattr("lucking.flows.stock_list._build_service", lambda _: Service())
    result = sync_stock_list.fn(
        scope_code="CN-S",
        scheduled_at=datetime(2026, 7, 26, 1, 0, tzinfo=UTC),
        schedule_slug="daily-stock-list",
    )
    assert result["status"] == "SUCCEEDED"
    assert (tmp_path / "stock-list-sync.jsonl").exists()


def test_thirty_repeated_flow_triggers_keep_same_authoritative_result(
    monkeypatch, tmp_path
) -> None:
    calls = 0

    class Service:
        def sync(self, command):
            nonlocal calls
            calls += 1
            return StockListSyncResult(
                "run-1",
                "same-key",
                SyncStatus.SUCCEEDED,
                1,
                command.scheduled_at.date(),
                "memory",
                1,
                1,
                0,
                0,
                0,
                0,
                0,
                1,
            )

    monkeypatch.setenv("STOCK_LIST_LOG_DIR", str(tmp_path))
    service = Service()
    monkeypatch.setattr("lucking.flows.stock_list._build_service", lambda _: service)
    results = [
        sync_stock_list.fn(
            scheduled_at=datetime(2026, 7, 26, 1, 0, tzinfo=UTC),
            schedule_slug="daily-stock-list",
        )
        for _ in range(30)
    ]
    assert calls == 30
    assert {result["run_id"] for result in results} == {"run-1"}
    assert {result["run_key"] for result in results} == {"same-key"}


def test_flow_failure_is_logged_and_rethrown(monkeypatch, tmp_path) -> None:
    class Service:
        def sync(self, command):
            raise RuntimeError("token=secret raw response")

    monkeypatch.setenv("STOCK_LIST_LOG_DIR", str(tmp_path))
    monkeypatch.setattr("lucking.flows.stock_list._build_service", lambda _: Service())
    import pytest

    with pytest.raises(RuntimeError):
        sync_stock_list.fn(
            scheduled_at=datetime(2026, 7, 26, 1, 0, tzinfo=UTC),
            schedule_slug="daily-stock-list",
        )
    content = (tmp_path / "stock-list-sync.jsonl").read_text()
    assert "stock_list_sync_failed" in content
    assert "secret" not in content
