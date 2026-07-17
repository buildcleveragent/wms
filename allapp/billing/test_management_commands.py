import datetime
from io import StringIO
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.inventory.models import PostingJournal
from allapp.locations.models import Warehouse
from allapp.tasking.models import WmsTask

pytestmark = pytest.mark.integration


class BillingManagementCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="BMC", name="Billing Command Owner")
        self.warehouse = Warehouse.objects.create(
            code="BMC-WH",
            name="Billing Command Warehouse",
        )
        self.user = get_user_model().objects.create_user(
            username="billing-command-user",
            warehouse=self.warehouse,
        )

    def test_billing_accrue_storage_scopes_owner_warehouse_and_reports_totals(self):
        out = StringIO()
        service_date = datetime.date(2026, 5, 1)

        with mock.patch(
            "allapp.billing.management.commands.billing_accrue_storage.accrue_storage_for_date",
            return_value=(2, 3),
        ) as mocked_accrue:
            call_command(
                "billing_accrue_storage",
                "--date",
                service_date.isoformat(),
                "--owner",
                str(self.owner.id),
                "--warehouse",
                str(self.warehouse.id),
                stdout=out,
            )

        mocked_accrue.assert_called_once_with(
            self.owner.id,
            self.warehouse.id,
            service_date,
        )
        self.assertIn("Storage accrued 2 events, 3 accruals", out.getvalue())

    def test_billing_generate_metrics_passes_range_types_and_scope_to_service(self):
        out = StringIO()

        with mock.patch(
            "allapp.billing.management.commands.billing_generate_metrics.generate_metrics_for_range",
            return_value={
                "created": 4,
                "updated": 1,
                "deleted_zero": 0,
                "skipped_manual": 0,
                "unsupported": 0,
                "noop": 2,
                "skipped_zero": 0,
            },
        ) as mocked_generate:
            call_command(
                "billing_generate_metrics",
                "--date-from",
                "2026-05-01",
                "--date-to",
                "2026-05-03",
                "--owner",
                str(self.owner.id),
                "--warehouse",
                str(self.warehouse.id),
                "--metric-type",
                "PALLET",
                "--metric-type",
                "CBM",
                "--overwrite",
                stdout=out,
            )

        mocked_generate.assert_called_once_with(
            self.owner.id,
            self.warehouse.id,
            datetime.date(2026, 5, 1),
            datetime.date(2026, 5, 3),
            metric_types=["PALLET", "CBM"],
            overwrite=True,
            allow_area_fallback=False,
        )
        self.assertIn("created=4, updated=1", out.getvalue())

    def test_billing_retry_failed_dry_run_does_not_call_accrual_service(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="BMC-RETRY-DRY",
            task_type=WmsTask.TaskType.PICK,
        )
        PostingJournal.objects.create(
            src_model="WmsTask",
            src_id=task.id,
            tx_type="POST",
            status="POSTED",
            message="BILLING_FAILED: timeout",
        )
        out = StringIO()

        with mock.patch(
            "allapp.billing.management.commands.billing_retry_failed.billing_services.accrue_for_posting"
        ) as mocked_accrue:
            call_command("billing_retry_failed", "--dry-run", stdout=out)

        mocked_accrue.assert_not_called()
        self.assertIn("DRY-RUN", out.getvalue())
        self.assertIn("found=1 retried=0 errors=0", out.getvalue())

    def test_billing_retry_failed_retries_and_rewrites_failure_marker(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="BMC-RETRY-OK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            posted_by=self.user,
        )
        journal = PostingJournal.objects.create(
            src_model="WmsTask",
            src_id=task.id,
            tx_type="POST",
            status="POSTED",
            message="BILLING_FAILED: temporary outage",
        )
        out = StringIO()

        with mock.patch(
            "allapp.billing.management.commands.billing_retry_failed.billing_services.accrue_for_posting"
        ) as mocked_accrue, mock.patch(
            "allapp.billing.management.commands.billing_retry_failed.billing_services.accrue_order_processing_for_task"
        ) as mocked_order_processing:
            call_command("billing_retry_failed", stdout=out)

        mocked_accrue.assert_called_once_with(task, journal, by_user=self.user)
        mocked_order_processing.assert_called_once_with(
            task,
            journal,
            by_user=self.user,
            allowed_methods=mock.ANY,
        )
        journal.refresh_from_db()
        self.assertIn("BILLING_RETRIED", journal.message)
        self.assertNotIn("BILLING_FAILED", journal.message)
        self.assertIn("found=1 retried=1 errors=0", out.getvalue())

    def test_billing_retry_failed_keeps_complete_retried_marker_for_long_message(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="BMC-RETRY-LONG",
            task_type=WmsTask.TaskType.RECEIVE,
            posted_by=self.user,
        )
        journal = PostingJournal.objects.create(
            src_model="WmsTask",
            src_id=task.id,
            tx_type="POST",
            status="POSTED",
            message=f"{'x' * 225}|BILLING_FAILED:timeout",
        )

        with mock.patch(
            "allapp.billing.management.commands.billing_retry_failed.billing_services.accrue_for_posting"
        ):
            call_command("billing_retry_failed")

        journal.refresh_from_db()
        self.assertLessEqual(len(journal.message), 255)
        self.assertTrue(journal.message.endswith("|BILLING_RETRIED"))
        self.assertNotIn("BILLING_FAILED", journal.message)
