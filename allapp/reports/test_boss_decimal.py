"""Check rendered JSON, not only Decimal objects in response.data."""

from decimal import Decimal

from django.test import TestCase

from allapp.billing.models import BillingAccrual
from allapp.inventory.models import InventoryDetail
from allapp.reports import tests as report_fixtures


class BossDecimalContractTests(TestCase):
    setUp = report_fixtures.BossDashboardApiTests.setUp

    def test_opt_in_decimal_json_preserves_mysql_money_and_quantity(self):
        amount = Decimal("9007199254740993.01")
        BillingAccrual.objects.filter(acc_fingerprint="boss-home-acc-a").update(
            amount=amount, tax_amount=Decimal("0.09")
        )
        params = {
            "warehouse": self.warehouse.pk,
            "owner": self.owner.pk,
            "decimal_format": "string",
        }
        overview = self.client.get("/api/billing/dashboard/warehouse-overview/", params)
        self.assertEqual(overview.status_code, 200)
        data = overview.json()
        self.assertEqual(data["summary"]["accruals_by_currency"][0]["total"], "9007199254740993.10")
        self.assertEqual(data["by_owner"][0]["total"], "9007199254740993.10")
        self.assertEqual(data["recent_accruals"][0]["amount"], str(amount))
        self.assertIsInstance(data["summary"]["accrual_count"], int)

        today = self.client.get("/api/reports/boss/today/", params)
        self.assertEqual(today.status_code, 200)
        rows = {row["key"]: row for row in today.json()["financial"]}
        self.assertEqual(rows["accruals"]["money"][0]["amount"], str(amount))

        quantity = Decimal("99999999999999.1234")
        InventoryDetail.objects.filter(pk=self.inventory_a.pk).update(
            onhand_qty=quantity, available_qty=quantity - self.inventory_a.locked_qty
        )
        inventory = self.client.get("/api/reports/boss/inventory/details/", params)
        self.assertEqual(inventory.status_code, 200)
        row = next(row for row in inventory.json()["results"] if row["id"] == self.inventory_a.pk)
        self.assertEqual(row["onhand_qty"], str(quantity))

    def test_default_decimal_representation_remains_compatible(self):
        url = "/api/billing/dashboard/warehouse-overview/"
        params = {"warehouse": self.warehouse.pk, "owner": self.owner.pk}
        default = self.client.get(url, params).json()
        exact = self.client.get(url, {**params, "decimal_format": "string"}).json()
        self.assertEqual(default.keys(), exact.keys())
        legacy_total = default["summary"]["accruals_by_currency"][0]["total"]
        self.assertIsInstance(legacy_total, (float, int))
        self.assertEqual(
            Decimal(str(legacy_total)),
            Decimal(exact["summary"]["accruals_by_currency"][0]["total"]),
        )
