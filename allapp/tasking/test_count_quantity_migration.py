"""Exercise the additive capacity migration against persisted legacy values."""

from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from allapp.tasking.test_count_acceptance import case as count_case_fixture

case = count_case_fixture


@pytest.mark.migrations
@pytest.mark.django_db(transaction=True)
def test_count_capacity_upgrade_preserves_legacy_values_and_constraints(case):
    assert connection.settings_dict["NAME"] == "wms_db_test"
    before = [("tasking", "0009_taskscanlog_barcode_resolution_snapshot")]
    after = [("tasking", "0010_count_quantity_capacity")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Task = apps.get_model("tasking", "WmsTask")
        Line = apps.get_model("tasking", "WmsTaskLine")
        Scan = apps.get_model("tasking", "TaskScanLog")
        Extra = apps.get_model("tasking", "CountLineExtra")
        task = Task.objects.create(
            task_no="COUNT-MIGRATION",
            task_type="COUNT",
            owner_id=case.owner.pk,
            warehouse_id=case.warehouse.pk,
        )
        line = Line.objects.create(
            task=task,
            product_id=case.product.pk,
            from_location_id=case.location.pk,
            qty_plan=Decimal("999999999999999.999"),
            qty_done=Decimal("1.123"),
        )
        scan = Scan.objects.create(
            task=task,
            task_line=line,
            owner_id=case.owner.pk,
            warehouse_id=case.warehouse.pk,
            product_id=case.product.pk,
            location_id=case.location.pk,
            lot_no="B" * 60,
            qty_base=Decimal("999999999999.999999"),
            qty_base_delta=Decimal("-999999999999.999999"),
            scan_snapshot_rev=0,
            fp="upgrade",
        )
        extra = Extra.objects.create(
            line=line,
            lot_no="B" * 60,
            qty_book=Decimal("2.1234"),
            qty_counted=Decimal("1.1234"),
            qty_diff=Decimal("-1"),
        )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        upgraded_line = apps.get_model("tasking", "WmsTaskLine").objects.get(pk=line.pk)
        upgraded_scan = apps.get_model("tasking", "TaskScanLog").objects.get(pk=scan.pk)
        upgraded_extra = apps.get_model("tasking", "CountLineExtra").objects.get(pk=extra.pk)
        assert upgraded_line.qty_plan == Decimal("999999999999999.9990")
        assert upgraded_line.qty_done == Decimal("1.1230")
        assert upgraded_scan.qty_base == Decimal("999999999999.999999")
        assert upgraded_scan.qty_base_delta == Decimal("-999999999999.999999")
        assert upgraded_extra.qty_book == Decimal("2.1234")
        assert upgraded_extra.lot_no == upgraded_scan.lot_no == "B" * 60
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN "
                "('tasking_wmstaskline', 'tasking_taskscanlog', 'tasking_countlineextra')"
            )
            types = {(table, column): kind for table, column, kind in cursor.fetchall()}
            for column in ("qty_plan", "qty_done"):
                assert types[("tasking_wmstaskline", column)] == "decimal(19,4)"
            for column in ("qty_base", "qty_base_delta"):
                assert types[("tasking_taskscanlog", column)] == "decimal(20,6)"
            for table in ("tasking_taskscanlog", "tasking_countlineextra"):
                assert types[(table, "lot_no")] == "varchar(64)"
            constraints = connection.introspection.get_constraints(cursor, "tasking_countlineextra")
            assert constraints["ck_cnt_diff_eq_c_minus_b"]["check"]
            assert constraints["ck_cnt_qc_ge0"]["check"]
    finally:
        MigrationExecutor(connection).migrate(after)
