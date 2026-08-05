import datetime

from django.test import SimpleTestCase

from allapp.billing.serializers import BillingPeriodInvoiceSerializer
from allapp.reports.boss_contract import (
    build_meta,
    normalize_currency,
    trend_granularity,
)


class BossContractUnitTests(SimpleTestCase):
    def test_currency_never_defaults_to_cny(self):
        self.assertEqual(normalize_currency(None), "UNKNOWN")
        self.assertEqual(normalize_currency(" usd "), "USD")

    def test_trend_granularity_uses_requested_range(self):
        start = datetime.date(2026, 1, 1)
        self.assertEqual(
            trend_granularity(start, start + datetime.timedelta(days=30)), "day"
        )
        self.assertEqual(
            trend_granularity(start, start + datetime.timedelta(days=31)), "week"
        )
        self.assertEqual(
            trend_granularity(start, start + datetime.timedelta(days=180)), "month"
        )

    def test_meta_distinguishes_complete_warning_and_unavailable(self):
        scope = {"warehouse": None, "owner": None}
        self.assertEqual(build_meta(scope=scope)["data_status"], "COMPLETE")
        warning_meta = build_meta(
            scope=scope,
            warnings=[{"code": "UNKNOWN_CURRENCY", "count": 1}],
        )
        self.assertEqual(warning_meta["data_status"], "WARNING")
        self.assertEqual(
            build_meta(scope=scope, unavailable=True)["data_status"], "UNAVAILABLE"
        )
        self.assertEqual(
            build_meta(scope=scope)["scope_fingerprint"],
            build_meta(scope={"owner": None, "warehouse": None})["scope_fingerprint"],
        )

    def test_invoice_due_date_is_required_and_not_before_issue_date(self):
        missing = BillingPeriodInvoiceSerializer(data={"issue_date": "2026-08-05"})
        self.assertFalse(missing.is_valid())
        invalid = BillingPeriodInvoiceSerializer(
            data={"issue_date": "2026-08-05", "due_date": "2026-08-04"}
        )
        self.assertFalse(invalid.is_valid())
        valid = BillingPeriodInvoiceSerializer(
            data={"issue_date": "2026-08-05", "due_date": "2026-08-05"}
        )
        self.assertTrue(valid.is_valid(), valid.errors)
