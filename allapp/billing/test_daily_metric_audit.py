import datetime
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.billing.enums import CalcMethod, ChargeType, MetricType, PeriodStatus, PricingStatus
from allapp.billing.models import (
    BillingEvent,
    BillingJobRun,
    BillingMetricDaily,
    BillingPeriod,
    BillingServiceContract,
)
from allapp.locations.models import Warehouse

pytestmark = pytest.mark.integration


class DailyMetricAuditTests(TestCase):
    def setUp(self):
        owner = Owner.objects.create(code="DMA", name="Daily audit owner")
        warehouse = Warehouse.objects.create(code="DMA", name="Daily audit warehouse")
        self.scope = {"owner_id": owner.id, "warehouse_id": warehouse.id}
        self.start = datetime.date(2026, 1, 1)

    def report(self, days, *flags):
        out = StringIO()

        def check_read_only(execute, sql, params, many, context):
            self.assertEqual(sql.lstrip().split()[0].upper(), "SELECT", sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(check_read_only):
            call_command(
                "billing_audit_daily_metrics",
                "--date-from",
                self.start.isoformat(),
                "--date-to",
                (self.start + datetime.timedelta(days=days - 1)).isoformat(),
                *flags,
                stdout=out,
            )
        return json.loads(out.getvalue())

    def job(self, offset, summary, **scope):
        return BillingJobRun.objects.create(
            **(scope or self.scope),
            job_name=BillingJobRun.JobName.DAILY_METRIC_GENERATION,
            status=BillingJobRun.Status.SUCCESS,
            service_date=self.start + datetime.timedelta(days=offset),
            finished_at=timezone.now(),
            summary=summary,
        )

    def test_prioritizes_single_preserves_unknown_and_excludes_locked_scope_days(self):
        counters = dict.fromkeys(
            [
                "created",
                "updated",
                "deleted_zero",
                "skipped_zero",
                "skipped_manual",
                "unsupported",
                "noop",
            ],
            0,
        )
        self.job(0, dict(counters, created=4))
        self.job(1, dict(counters, created=1, approximate=1))
        self.job(2, dict(counters, skipped_zero=2))
        self.job(3, {"created": 1})
        other = Owner.objects.create(code="DMO", name="Other audit owner")
        self.job(0, dict(counters, created=1), **dict(self.scope, owner_id=other.id))
        for offset, status in (
            (4, PeriodStatus.CLOSED),
            (5, PeriodStatus.INVOICED),
            (6, PeriodStatus.OPEN),
        ):
            period_day = self.start + datetime.timedelta(days=offset)
            BillingPeriod.objects.create(
                **self.scope,
                start_date=period_day,
                end_date=period_day,
                label=period_day.isoformat(),
                status=status,
            )
            self.job(offset, dict(counters, noop=1))
        rows = self.report(
            7,
            "--owner",
            str(self.scope["owner_id"]),
            "--warehouse",
            str(self.scope["warehouse_id"]),
        )["rows"]
        self.assertEqual(
            [row["service_date"] for row in rows],
            ["2026-01-02", "2026-01-07", "2026-01-03", "2026-01-01", "2026-01-04"],
        )
        self.assertEqual(rows[0]["evidence"], "旧记录最近一次只处理一项")
        self.assertIsNone(rows[0]["missing_metric_types"])
        self.assertIsNone(rows[0]["period_id"])
        self.assertIsNotNone(rows[1]["period_id"])
        self.assertEqual(rows[2]["evidence"], "旧记录最近一次只处理部分项")
        self.assertEqual(rows[3]["evidence"], rows[4]["evidence"])

    def test_completed_single_request_stays_visible_with_current_coverage(self):
        coverage = {
            metric: {
                "action": "skipped_zero",
                "overwrite": False,
                "source": f"AUTO:{metric}",
                "source_quality": "VERIFIED",
            }
            for metric in MetricType.values
        }
        coverage[MetricType.AREA_M2]["allow_area_fallback"] = False
        self.job(
            0, {"coverage_version": 1, "had_single_metric_run": True, "metric_coverage": coverage}
        )
        row = self.report(1)["rows"][0]
        self.assertEqual(row["evidence"], "曾执行单项")
        self.assertEqual(row["missing_metric_types"], [])
        self.assertEqual(row["metric_coverage"]["CBM"]["action"], "skipped_zero")
        self.assertEqual(row["stored_metrics"], [])

    def test_contract_and_direct_generation_without_job_report_metric_event_accrual_separately(
        self,
    ):
        end = self.start + datetime.timedelta(days=11)
        BillingServiceContract.objects.create(
            **self.scope,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            effective_from=self.start,
            effective_to=end,
            source_type="DAILY_METRIC",
        )
        for day in (self.start, end):
            metric = BillingMetricDaily.objects.create(
                **self.scope, service_date=day, metric_type=MetricType.CBM, value=2, source="manual"
            )
        BillingEvent.objects.create(
            **self.scope,
            metric=metric,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            service_date=end,
            pricing_status=PricingStatus.ACCRUED,
            priced_at=timezone.now(),
            event_fp="daily-audit-missing-accrual",
        )
        report = self.report(12)
        rows = report["rows"]
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(row["evidence"] == "无调度记录" for row in rows))
        self.assertEqual(rows[0]["stored_metrics"][0]["value"], "2.0000")
        self.assertEqual(rows[0]["contract_gaps"], [{"metric_type": "CBM", "missing": "event"}])
        self.assertTrue(
            all(
                row["contract_gaps"] == [{"metric_type": "CBM", "missing": "metric"}]
                for row in rows[1:-1]
            )
        )
        self.assertEqual(rows[-1]["contract_gaps"], [])
        blockers = {b["code"]: b for b in report["close_checks"][0]["blockers"]}
        self.assertEqual(blockers["BILLING_CONTRACT_METRIC_EVENT_MISSING"]["count"], 11)
        self.assertEqual(
            blockers["BILLING_EVENT_ACCRUAL_MISSING"]["samples"][0]["service_date"], end.isoformat()
        )

    def test_rejects_reversed_or_invalid_date_range(self):
        for date_from in ("2026-01-03", "not-a-date"):
            with self.subTest(date_from=date_from), self.assertRaises(CommandError):
                call_command(
                    "billing_audit_daily_metrics",
                    "--date-from",
                    date_from,
                    "--date-to",
                    "2026-01-02",
                )
