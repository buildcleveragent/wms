import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from allapp.billing.enums import BundleScope, BundleType, CapMode
from allapp.billing.services._common import _finalize_daily_price
from allapp.billing.services._metrics import _inventory_rows_source_quality
from allapp.billing.enums import SourceQuality
from allapp.inventory.models import InventorySnapshotDaily


class BillingIntegrityPricingUnitTests(SimpleTestCase):
    def test_minimum_charge_is_clipped_by_remaining_daily_cap(self):
        rule = SimpleNamespace(
            id=7,
            min_charge=Decimal("10.00"),
            cap_mode=CapMode.PER_DAY,
            cap_amount=Decimal("5.00"),
            bundle_scope=BundleScope.NONE,
            bundle_type=BundleType.CAP,
            bundle_price=None,
            bundle_key="",
        )
        with mock.patch(
            "allapp.billing.services._common._sum_amount_rule_day",
            return_value=Decimal("3.00"),
        ):
            result = _finalize_daily_price(
                rule,
                1,
                1,
                datetime.date(2026, 8, 5),
                Decimal("1"),
                Decimal("1.00"),
            )
        self.assertEqual(result.raw_amount, Decimal("1.00"))
        self.assertEqual(result.minimum_amount, Decimal("10.00"))
        self.assertEqual(result.final_amount, Decimal("2.00"))
        self.assertIn("DAILY_RULE_CAP", result.limit_reasons)

    def test_approximate_snapshot_source_propagates_to_metric_quality(self):
        rows = [
            SimpleNamespace(
                snapshot_source=InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX
            )
        ]
        self.assertEqual(
            _inventory_rows_source_quality(rows), SourceQuality.APPROXIMATE
        )
