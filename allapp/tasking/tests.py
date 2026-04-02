from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine


class TaskingWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Tasking", code="OWN-TASK")
        self.warehouse = Warehouse.objects.create(code="WH-TASK-1", name="Warehouse Tasking 1")
        self.other_warehouse = Warehouse.objects.create(code="WH-TASK-2", name="Warehouse Tasking 2")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWTASK1",
            name="Subwarehouse Tasking 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWTASK2",
            name="Subwarehouse Tasking 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWTASK1-01-01-01",
            name="Tasking Location 1",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWTASK2-01-01-01",
            name="Tasking Location 2",
        )

    def test_wms_task_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            WmsTask.objects.create(
                owner=self.owner,
                task_no="TASK-NO-WH",
                task_type=WmsTask.TaskType.RECEIVE,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_task_scan_log_derives_warehouse_from_task(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-SCAN-1",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        line = WmsTaskLine.objects.create(task=task)

        scan = TaskScanLog.objects.create(
            owner=self.owner,
            task=task,
            task_line=line,
            qty_base_delta=Decimal("1.000000"),
            fp="task-scan-fp-1",
            scan_snapshot_rev=0,
        )

        self.assertEqual(scan.warehouse_id, self.warehouse.id)

    def test_task_scan_log_rejects_location_warehouse_mismatch(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-SCAN-2",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        line = WmsTaskLine.objects.create(task=task)

        with self.assertRaises(ValidationError) as exc:
            TaskScanLog.objects.create(
                owner=self.owner,
                task=task,
                task_line=line,
                location=self.other_location,
                qty_base_delta=Decimal("1.000000"),
                fp="task-scan-fp-2",
                scan_snapshot_rev=0,
            )

        self.assertIn("location", exc.exception.message_dict)
