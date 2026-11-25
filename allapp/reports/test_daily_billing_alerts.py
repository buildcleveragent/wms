from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.test import TestCase, override_settings

from allapp.baseinfo.models import Owner
from allapp.billing.enums import CalcMethod, ChargeType, MetricType, PeriodStatus
from allapp.billing.models import BillingJobRun, BillingPeriod, BillingServiceContract
from allapp.locations.models import Warehouse
from allapp.reports.models import AlertCase, BossCheckState
from allapp.reports.services_alert_cases import sync_alert_cases
from allapp.reports.services_daily_billing_alerts import ALERT_TYPE

pytestmark = pytest.mark.integration


@override_settings(
    BILLING_METRIC_SCHEDULER_HOUR=1,
    BILLING_METRIC_SCHEDULER_MINUTE=5,
    BILLING_METRIC_SCHEDULER_LOOKBACK_DAYS=3,
    BILLING_METRIC_ALERT_GRACE_MINUTES=60,
)
class DailyBillingAlertTests(TestCase):
    day = date(2026, 5, 1)
    due = datetime(2026, 5, 2, 2, 5)

    def setUp(self):
        self.owner = Owner.objects.create(code="DBA", name="合同货主")
        self.warehouse = Warehouse.objects.create(code="DBA", name="合同仓库")
        self.scope = {"owner": self.owner, "warehouse": self.warehouse}
        self.cases = AlertCase.objects.filter(alert_type=ALERT_TYPE)

    def contract(
        self, method, *, start=None, end=None, active=True, source="DAILY_METRIC", **scope
    ):
        return BillingServiceContract.objects.create(
            **(self.scope | scope),
            charge_type=ChargeType.STORAGE,
            calc_method=method,
            effective_from=start or self.day,
            effective_to=end or self.day,
            is_active=active,
            source_type=source,
        )

    def job(self, day=None, **fields):
        return BillingJobRun.objects.create(
            **self.scope,
            service_date=day or self.day,
            job_name=BillingJobRun.JobName.DAILY_METRIC_GENERATION,
            status=BillingJobRun.Status.SUCCESS,
            finished_at=self.due,
            **fields,
        )

    def summary(self, *, retry=(), **actions):
        coverage = {key: {"action": action, "overwrite": False} for key, action in actions.items()}
        if MetricType.AREA_M2 in coverage:
            coverage[MetricType.AREA_M2]["allow_area_fallback"] = False
        return {
            "coverage_version": 1,
            "metric_coverage": coverage,
            "retry_metric_types": list(retry),
        }

    def sync(self, at):
        with patch("allapp.reports.services_alert_cases.timezone.now", return_value=at):
            return sync_alert_cases()

    def test_missing_job_partial_coverage_zero_manual_and_reopen_share_one_case(self):
        self.contract(CalcMethod.PERCENT_OF_ORDER_AMOUNT)
        self.contract(CalcMethod.PER_CBM_DAY)
        self.sync(self.due - timedelta(seconds=1))
        self.assertFalse(self.cases.exists())
        self.sync(self.due)
        case = self.cases.get()
        self.assertEqual(case.detail["business_due_at"], self.due.isoformat())
        self.assertIn("无调度记录", case.title)
        self.assertIsNone(case.detail["job_id"])
        self.assertEqual(case.detail["record_no"], self.day.isoformat())
        self.assertEqual(case.detail["missing_metric_types"], ["CBM", "ORDER_AMT"])
        case.status = AlertCase.Status.ACKNOWLEDGED
        case.save(update_fields=["status"])

        job = self.job(summary=self.summary(ORDER_AMT="skipped_zero"))
        self.sync(self.due)
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.ACKNOWLEDGED)
        self.assertEqual(case.detail["job_id"], job.id)
        self.assertEqual(case.detail["missing_metric_types"], ["CBM"])
        self.assertIn("体积", case.title)
        self.assertEqual(case.history.count(), 1)
        job.summary = self.summary(ORDER_AMT="skipped_zero", CBM="skipped_manual")
        job.save(update_fields=["summary"])
        self.sync(self.due)
        self.sync(self.due)
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.RESOLVED)
        self.assertEqual(
            list(case.history.values_list("action", flat=True)), ["DISCOVER", "AUTO_RESOLVE"]
        )
        job.summary = self.summary(ORDER_AMT="skipped_zero", CBM="skipped_manual", retry=["CBM"])
        job.save(update_fields=["summary"])
        self.sync(self.due)
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.OPEN)
        self.assertEqual(case.detail["retry_metric_types"], ["CBM"])
        self.assertEqual(case.history.last().action, "REOPEN")

    def test_legacy_unsupported_approximate_and_unrelated_retries_are_distinct(self):
        self.contract(CalcMethod.PER_AREA_MONTH)
        job = self.job(summary={"created": 4})
        self.sync(self.due)
        case = self.cases.get()
        self.assertIn("逐项执行记录", case.title)
        job.summary = self.summary(AREA_M2="unsupported")
        job.save(update_fields=["summary"])
        self.sync(self.due)
        case.refresh_from_db()
        self.assertIn("计算来源", case.title)
        self.assertEqual(case.detail["unsupported_metric_types"], ["AREA_M2"])
        job.summary = self.summary(AREA_M2="updated", retry=["ORDER_AMT"])
        job.summary["metric_coverage"]["AREA_M2"]["source_quality"] = "APPROXIMATE"
        job.status = BillingJobRun.Status.WARNING
        job.save(update_fields=["status", "summary"])
        self.sync(self.due)
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.RESOLVED)
        warning = AlertCase.objects.get(alert_type="BILLING_JOB_FAILURE")
        self.assertEqual(warning.status, AlertCase.Status.OPEN)

    def test_no_obligation_dates_and_closed_periods_are_excluded(self):
        self.contract(CalcMethod.PER_DAY_ONHAND_BASE)
        self.contract(CalcMethod.PER_TASK, source="TASK")
        self.contract(CalcMethod.PER_AREA_MONTH, active=False)
        self.contract(
            CalcMethod.PER_CBM_DAY,
            start=self.day + timedelta(days=1),
            end=self.day + timedelta(days=2),
        )
        self.contract(
            CalcMethod.PER_CBM_DAY,
            start=self.day - timedelta(days=6),
            end=self.day - timedelta(days=4),
        )
        for status in (PeriodStatus.CLOSED, PeriodStatus.INVOICED):
            warehouse = Warehouse.objects.create(code=status, name=status)
            self.contract(CalcMethod.PER_CBM_DAY, warehouse=warehouse)
            BillingPeriod.objects.create(
                owner=self.owner,
                warehouse=warehouse,
                start_date=self.day,
                end_date=self.day,
                label=status,
                status=status,
            )
        self.sync(self.due)
        self.assertFalse(self.cases.exists())
        self.assertFalse(BillingJobRun.objects.exists())

    def test_first_scan_backlog_and_previously_open_cases_outside_lookback(self):
        self.contract(
            CalcMethod.PER_PALLET_DAY,
            start=self.day - timedelta(days=10),
            end=self.day + timedelta(days=6),
        )
        # Old success records from the existing billing-health rule must not bootstrap this rule.
        BossCheckState.objects.create(
            key="BILLING_HEALTH", checked_at=self.due - timedelta(days=10)
        )
        self.sync(self.due)
        self.assertEqual(self.cases.count(), 3)
        oldest = self.day - timedelta(days=2)
        old_case = self.cases.get(detail__service_date=oldest.isoformat())
        self.sync(self.due + timedelta(days=6))
        self.assertEqual(self.cases.filter(status="OPEN").count(), 9)
        self.job(day=oldest, summary=self.summary(PALLET="noop"))
        self.sync(self.due + timedelta(days=7))
        old_case.refresh_from_db()
        self.assertEqual(old_case.status, AlertCase.Status.RESOLVED)
        self.assertEqual(self.cases.filter(status="OPEN").count(), 8)

    def test_closing_period_keeps_existing_case_until_evidenced_or_manually_closed(self):
        self.contract(CalcMethod.PER_CBM_DAY)
        self.sync(self.due)
        case = self.cases.get()
        period = BillingPeriod.objects.create(
            **self.scope,
            start_date=self.day,
            end_date=self.day,
            label="Closed",
            status=PeriodStatus.CLOSED,
        )
        self.sync(self.due)
        self.sync(self.due + timedelta(days=8))
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.OPEN)
        self.assertEqual(case.history.count(), 1)
        job = self.job(summary=self.summary(CBM="skipped_zero"))
        self.sync(self.due + timedelta(days=8))
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.RESOLVED)
        period.status = PeriodStatus.INVOICED
        period.save(update_fields=["status"])
        case.status = AlertCase.Status.CLOSED
        case.save(update_fields=["status"])
        job.summary = self.summary()
        job.save(update_fields=["summary"])
        with self.settings(BILLING_METRIC_SCHEDULER_LOOKBACK_DAYS=10):
            self.sync(self.due + timedelta(days=8))
        case.refresh_from_db()
        self.assertEqual(case.status, AlertCase.Status.CLOSED)

    @override_settings(BILLING_METRIC_SCHEDULER_HOUR=23, BILLING_METRIC_SCHEDULER_MINUTE=50)
    def test_grace_can_cross_midnight(self):
        self.contract(CalcMethod.PER_CBM_DAY)
        due = datetime(2026, 5, 3, 0, 50)
        self.sync(due - timedelta(seconds=1))
        self.assertFalse(self.cases.exists())
        self.sync(due)
        self.assertEqual(self.cases.get().detail["business_due_at"], due.isoformat())

    def test_failed_scan_keeps_cursor_and_open_case(self):
        self.contract(CalcMethod.PER_CBM_DAY)
        self.sync(self.due)
        cursor = BossCheckState.objects.get(key=ALERT_TYPE)
        with patch(
            "allapp.reports.services_alert_cases.overdue_daily_metric_sources",
            side_effect=RuntimeError("simulated query failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.sync(self.due + timedelta(days=1))
        cursor.refresh_from_db()
        self.assertEqual(cursor.checked_at, self.due)
        self.assertEqual(self.cases.get().status, AlertCase.Status.OPEN)
