from pathlib import Path

import yaml

from lucking.task_catalog import SCHEDULED_TASKS


def test_catalog_matches_every_business_sync_schedule_in_prefect_yaml() -> None:
    document = yaml.safe_load(Path("prefect.yaml").read_text())
    configured = {
        schedule["slug"]
        for deployment in document["deployments"]
        if "daily_task_summary.py" not in deployment["entrypoint"]
        for schedule in deployment.get("schedules", [])
    }
    catalog = {task.schedule_slug for task in SCHEDULED_TASKS}
    assert catalog == configured
    assert len(catalog) == len(SCHEDULED_TASKS)
    assert all(task.timezone == "Asia/Shanghai" for task in SCHEDULED_TASKS)


def test_catalog_excludes_manual_backfills_and_daily_summary_itself() -> None:
    keys = {task.task_key for task in SCHEDULED_TASKS}
    assert all("backfill" not in key for key in keys)
    assert "daily-task-summary" not in keys
