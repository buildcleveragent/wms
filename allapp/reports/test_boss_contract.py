import datetime

from django.test import SimpleTestCase

from allapp.billing.serializers import BillingPeriodInvoiceSerializer
from allapp.reports.boss_contract import (
    build_meta,
    normalize_currency,
    parse_boss_date_range,
    trend_granularity,
)


class BossContractUnitTests(SimpleTestCase):
    def test_date_defaults_and_inclusive_range_boundary(self):
        today = datetime.date(2026, 10, 4)
        self.assertEqual(parse_boss_date_range(today=today), (today.replace(day=1), today))
        self.assertEqual(
            parse_boss_date_range(raw_to="2024-02-29", today=today),
            (datetime.date(2024, 2, 1), datetime.date(2024, 2, 29)),
        )
        start = today - datetime.timedelta(days=366)
        self.assertEqual(parse_boss_date_range(str(start), str(today), today=today), (start, today))

    def test_invalid_dates_are_validation_errors(self):
        today = datetime.date(2026, 10, 4)
        pairs = [
            (None, "bad"),
            (None, "2026-02-30"),
            ("bad", None),
            (None, []),
            (None, {}),
            (None, 123),
            ("2026-10-04", "2026-10-03"),
            (None, "2026-10-05"),
            (str(today - datetime.timedelta(days=367)), str(today)),
        ]
        for start, end in pairs:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                parse_boss_date_range(start, end, today=today)

    def test_currency_never_defaults_to_cny(self):
        self.assertEqual(normalize_currency(None), "UNKNOWN")
        self.assertEqual(normalize_currency(" usd "), "USD")

    def test_trend_granularity_uses_requested_range(self):
        start = datetime.date(2026, 1, 1)
        self.assertEqual(trend_granularity(start, start + datetime.timedelta(days=30)), "day")
        self.assertEqual(trend_granularity(start, start + datetime.timedelta(days=31)), "week")
        self.assertEqual(trend_granularity(start, start + datetime.timedelta(days=180)), "month")

    def test_meta_distinguishes_complete_warning_and_unavailable(self):
        scope = {"warehouse": None, "owner": None}
        self.assertEqual(build_meta(scope=scope)["data_status"], "COMPLETE")
        warning_meta = build_meta(
            scope=scope,
            warnings=[{"code": "UNKNOWN_CURRENCY", "count": 1}],
        )
        self.assertEqual(warning_meta["data_status"], "WARNING")
        self.assertEqual(build_meta(scope=scope, unavailable=True)["data_status"], "UNAVAILABLE")
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
