from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from allapp.reports.models import AggBillingDaily, AggThroughputDaily, DateDim

pytestmark = pytest.mark.integration


class ReportsManagementCommandTests(TestCase):
    def test_etl_seed_datedim_creates_range_idempotently(self):
        out = StringIO()

        call_command(
            "etl_seed_datedim",
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-03",
            stdout=out,
        )
        call_command(
            "etl_seed_datedim",
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-03",
            stdout=StringIO(),
        )

        self.assertEqual(DateDim.objects.count(), 3)
        self.assertTrue(DateDim.objects.get(date_key=20260501).is_month_start)
        self.assertIn("DateDim OK: 3 days", out.getvalue())

    def test_etl_seed_datedim_rejects_reverse_range(self):
        with self.assertRaises(CommandError):
            call_command(
                "etl_seed_datedim",
                "--start",
                "2026-05-03",
                "--end",
                "2026-05-01",
            )

    def test_refresh_agg_reports_creates_date_dim_and_is_idempotent_without_facts(self):
        out = StringIO()

        call_command("refresh_agg_reports", "--date", "2026-05-04", stdout=out)
        call_command("refresh_agg_reports", "--date", "2026-05-04", stdout=StringIO())

        self.assertTrue(DateDim.objects.filter(date_key=20260504).exists())
        self.assertEqual(AggThroughputDaily.objects.count(), 0)
        self.assertEqual(AggBillingDaily.objects.count(), 0)
        self.assertIn("聚合刷新完成：2026-05-04", out.getvalue())

    def test_refresh_agg_reports_rejects_invalid_date(self):
        with self.assertRaises(CommandError):
            call_command("refresh_agg_reports", "--date", "not-a-date")
