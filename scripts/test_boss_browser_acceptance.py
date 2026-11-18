"""Real Boss H5/API acceptance using synthetic data in the canonical MySQL database."""

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from openpyxl import load_workbook

from allapp.accounts.models import UserRoleScope
from allapp.billing.models import Bill, BillingAccrual, BillingRule, BillLine
from allapp.reports import tests as report_fixtures

ROOT = Path(__file__).resolve().parents[1]


@override_settings(ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"])
class BossBrowserAcceptanceTests(StaticLiveServerTestCase):
    def test_login_dashboard_billing_and_export_with_real_http(self):
        report_fixtures.BossDashboardApiTests.setUp(self)
        self.user.set_password("synthetic-boss-browser-password")
        self.user.save(update_fields=["password"])
        other_user = get_user_model().objects.create_user(
            username="isolated-browser-boss", password="synthetic-boss-browser-password"
        )
        report_fixtures._assign_report_role(
            other_user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.other_warehouse,
        )
        bill = Bill.objects.get(invoice_no="BILL-BOSS-A")
        accrual = BillingAccrual.objects.get(acc_fingerprint="boss-home-acc-a")
        usd_rule = BillingRule.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=accrual.charge_type,
            calc_method=self.rule_a.calc_method,
            unit_price=Decimal("1"),
            currency="USD",
        )
        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=accrual.charge_type,
            service_date=self.today,
            currency="USD",
            rule=usd_rule,
            quantity=Decimal("1"),
            unit_price=Decimal("1"),
            amount=Decimal("9007199254740993.01"),
            tax_amount=Decimal("0.09"),
            acc_fingerprint="boss-browser-large-decimal",
        )
        BillLine.objects.create(
            bill=bill,
            accrual=accrual,
            charge_type=accrual.charge_type,
            service_date=accrual.service_date,
            quantity=accrual.quantity,
            unit_price=accrual.unit_price,
            amount=accrual.amount,
            tax_amount=accrual.tax_amount,
            description="合成测试发运费用",
        )
        artifacts = Path(os.environ["WMS_BROWSER_ARTIFACTS"]).resolve()
        artifacts.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", str(ROOT / "scripts/boss-browser-acceptance.mjs")],
            input=json.dumps(
                {
                    "backend": self.live_server_url,
                    "artifacts": str(artifacts),
                    "username": self.user.username,
                    "otherUsername": other_user.username,
                    "password": "synthetic-boss-browser-password",
                    "warehouse": self.warehouse.pk,
                    "otherWarehouse": self.other_warehouse.pk,
                    "owner": self.owner.pk,
                    "bill": bill.pk,
                    "accrual": accrual.pk,
                    "dateFrom": str(min(self.month_start, bill.issue_date)),
                    "dateTo": str(self.today),
                }
            ),
            text=True,
            capture_output=True,
            timeout=360,
        )
        (artifacts / "browser.log").write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for file in artifacts.glob("*.xlsx"):
            with file.open("rb") as stream:
                workbook = load_workbook(stream, read_only=True, data_only=True)
                self.assertGreaterEqual(len(workbook.sheetnames), 1)
                values = [
                    str(value)
                    for sheet in workbook
                    for row in sheet.values
                    for value in row
                    if value is not None
                ]
                self.assertNotIn(self.other_warehouse.name, values)
                metadata = dict(list(workbook["报告信息"].values)[1:])
                self.assertEqual(metadata["仓库范围"], self.warehouse.name)
                self.assertEqual(metadata["货主"], self.owner.name)
                self.assertEqual(metadata["日期开始"], str(min(self.month_start, bill.issue_date)))
                self.assertEqual(metadata["日期结束"], str(self.today))
                blocks = {
                    name: json.loads(value)
                    for name, part, value in list(workbook["报表数据"].values)[1:]
                }
                self.assertEqual(blocks["outstanding_by_currency"][0]["total"], "110.00")
                self.assertEqual([row["invoice_no"] for row in blocks["bills"]], ["BILL-BOSS-A"])
                workbook.close()
        self.assertEqual(len(list(artifacts.glob("*.xlsx"))), 2)
