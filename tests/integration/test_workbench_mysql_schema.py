from pathlib import Path

from sqlalchemy import BigInteger

from lucking.models.workbench import WORKBENCH_TABLES

EXPECTED_TABLES = {
    "app_user",
    "important_date",
    "watchlist_group",
    "watchlist_member",
    "daily_task_summary",
    "daily_task_summary_item",
    "daily_task_notification_attempt",
}


def test_workbench_tables_follow_physical_governance() -> None:
    assert set(WORKBENCH_TABLES) == EXPECTED_TABLES

    for table in WORKBENCH_TABLES.values():
        assert table.comment
        assert table.c.id.primary_key
        assert table.c.id.autoincrement is True
        assert isinstance(table.c.id.type, BigInteger)
        assert table.c.id.comment == "主键ID"
        assert table.c.created_at.comment == "创建时间"
        assert table.c.updated_at.comment == "更新时间"
        assert table.c.created_at.server_default is not None
        assert table.c.updated_at.server_default is not None
        assert all(column.comment for column in table.columns)


def test_workbench_tables_define_business_keys_foreign_keys_and_indexes() -> None:
    assert WORKBENCH_TABLES["important_date"].c.user_id.foreign_keys
    assert WORKBENCH_TABLES["watchlist_group"].c.user_id.foreign_keys
    assert WORKBENCH_TABLES["watchlist_member"].c.group_id.foreign_keys
    assert WORKBENCH_TABLES["watchlist_member"].c.stock_id.foreign_keys
    assert WORKBENCH_TABLES["daily_task_summary_item"].c.summary_id.foreign_keys
    assert WORKBENCH_TABLES["daily_task_notification_attempt"].c.summary_id.foreign_keys

    constraint_names = {
        constraint.name
        for table in WORKBENCH_TABLES.values()
        for constraint in table.constraints
        if constraint.name
    }
    assert {
        "uq_app_user_user_id",
        "uq_app_user_username",
        "uq_important_date_owner_date_title",
        "uq_watchlist_group_owner_name",
        "uq_watchlist_member_group_stock",
        "uq_daily_task_summary_business_date",
        "uq_daily_task_summary_item_task",
        "uq_daily_task_notification_attempt_no",
    } <= constraint_names

    index_names = {index.name for table in WORKBENCH_TABLES.values() for index in table.indexes}
    assert {
        "ix_important_date_owner_date",
        "ix_watchlist_group_owner_sort",
        "ix_watchlist_member_group_sort",
        "ix_daily_task_summary_item_status",
        "ix_daily_task_notification_attempt_summary",
    } <= index_names


def test_migration_preserves_mysql_comments_and_database_timestamps() -> None:
    migration = Path("migrations/versions/007_create_workbench_tables.py").read_text()

    for table_name in EXPECTED_TABLES:
        assert f'"{table_name}"' in migration
    assert migration.count('comment="主键ID"') >= 1
    assert 'server_default=sa.text("CURRENT_TIMESTAMP")' in migration
    assert "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in migration
    assert migration.count("mysql_comment=") == 7
