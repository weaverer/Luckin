import json
from pathlib import Path

from lucking.flows.broker_recommendation import _merge_log_fields
from lucking.logging import JsonlLogStore


def test_broker_log_whitelist_excludes_secrets_payload_and_physical_id(
    tmp_path: Path,
) -> None:
    store = JsonlLogStore(tmp_path, filename="broker.jsonl")
    store.write(
        "broker_recommendation_sync_failed",
        run_id="run-uuid",
        attempt_id="attempt-uuid",
        provider_page_count=3,
        token="secret",
        payload={"raw": "row"},
        id=999,
        error_summary="token=secret password=hunter2",
    )
    entry = json.loads(store.path.read_text())
    assert entry["run_id"] == "run-uuid"
    assert entry["attempt_id"] == "attempt-uuid"
    assert entry["provider_page_count"] == 3
    assert "token" not in entry and "payload" not in entry and "id" not in entry
    assert "secret" not in entry["error_summary"]


def test_overlapping_flow_log_fields_are_merged_before_keyword_expansion() -> None:
    fields = _merge_log_fields(
        {"run_kind": "SCHEDULED", "flow_run_id": "flow-id"},
        {"run_kind": "BACKFILL", "run_id": "run-id"},
    )

    assert fields == {
        "run_kind": "BACKFILL",
        "flow_run_id": "flow-id",
        "run_id": "run-id",
    }
