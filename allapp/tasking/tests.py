import threading
from unittest import mock

from django.contrib.auth import get_user_model
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Owner
from allapp.inventory.models import PostingJournal
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.services_posting import post_task


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
        self.superuser = get_user_model().objects.create_superuser(
            username="tasking-admin",
            email="tasking-admin@example.com",
            password="x",
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

    def test_post_task_is_idempotent(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-POST-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )

        with mock.patch(
            "allapp.tasking.services_posting.execute_posting_handler",
            return_value=2,
        ) as mocked_handler:
            first = post_task(task.id, by_user=self.superuser, note="first post")
            second = post_task(task.id, by_user=self.superuser, note="second post")

        task.refresh_from_db()
        journal = PostingJournal.objects.get(src_model="WmsTask", src_id=task.id, tx_type="POST")
        self.assertEqual(mocked_handler.call_count, 1)
        self.assertEqual(first["tx_created"], 2)
        self.assertEqual(second["tx_created"], 0)
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)


class TaskPostingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Tasking Concurrent", code="OWN-TASK-C")
        self.warehouse = Warehouse.objects.create(code="WH-TASK-C", name="Warehouse Tasking Concurrent")
        self.superuser = get_user_model().objects.create_superuser(
            username="tasking-concurrent-admin",
            email="tasking-concurrent-admin@example.com",
            password="x",
        )

    def test_post_task_executes_once_under_concurrency(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-POST-CONCURRENT-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )

        handler_entered = threading.Event()
        release_handler = threading.Event()
        handler_calls = 0
        handler_lock = threading.Lock()
        results = [None, None]
        errors = []

        def fake_execute_posting_handler(*, task, note):
            nonlocal handler_calls
            with handler_lock:
                handler_calls += 1
            handler_entered.set()
            if not release_handler.wait(timeout=5):
                raise AssertionError("timed out waiting to release task posting concurrent test")
            return 2

        def invoke(index, note):
            close_old_connections()
            try:
                results[index] = post_task(task.id, by_user=self.superuser, note=note)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch(
            "allapp.tasking.services_posting.execute_posting_handler",
            side_effect=fake_execute_posting_handler,
        ):
            thread1 = threading.Thread(target=invoke, args=(0, "first concurrent post"))
            thread1.start()
            self.assertTrue(handler_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke, args=(1, "second concurrent post"))
            thread2.start()

            release_handler.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent task posting threads did not finish")
        if errors:
            raise errors[0]

        task.refresh_from_db()
        journal = PostingJournal.objects.get(src_model="WmsTask", src_id=task.id, tx_type="POST")
        self.assertEqual(handler_calls, 1)
        self.assertEqual(sorted(result["tx_created"] for result in results), [0, 2])
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
