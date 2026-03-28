import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import models
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.baseinfo.models import Owner
from allapp.billing.enums import AccrualStatus, BundleScope, BundleType, CalcMethod, ChargeType, PeriodStatus
from allapp.billing.models import Bill, BillingAccrual, BillingPeriod, BillingRule
from allapp.billing.services import accrue_order_processing_from_posted, generate_invoice_for_period, lock_period
from allapp.locations.models import Warehouse
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from wmsmaster.views import profile_view


class BillingServiceTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner A", code="OWNA")
        self.other_owner = Owner.objects.create(name="Owner B", code="OWNB")
        self.warehouse = Warehouse.objects.create(code="WH1", name="Warehouse 1")
        self.user = get_user_model().objects.create_user(
            username="billing-user",
            password="x",
            warehouse=self.warehouse,
        )

    def _create_rule(
        self,
        *,
        owner=None,
        charge_type=ChargeType.DISPATCH,
        calc_method=CalcMethod.PER_ORDER,
        unit_price="10.00",
        effective_from=None,
        effective_to=None,
        bundle_key="",
        bundle_scope=BundleScope.NONE,
        bundle_type=BundleType.CAP,
        bundle_price=None,
    ):
        return BillingRule.objects.create(
            owner=owner or self.owner,
            warehouse=self.warehouse,
            charge_type=charge_type,
            calc_method=calc_method,
            unit_price=Decimal(unit_price),
            effective_from=effective_from,
            effective_to=effective_to,
            bundle_key=bundle_key,
            bundle_scope=bundle_scope,
            bundle_type=bundle_type,
            bundle_price=Decimal(bundle_price) if bundle_price is not None else None,
        )

    def _create_task(self, task_no: str, task_type=WmsTask.TaskType.DISPATCH):
        return WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no=task_no,
            task_type=task_type,
        )

    def _create_scan_log(self, task, task_line, service_date: datetime.date, *, fp: str, label_key: str):
        posted_at = datetime.datetime.combine(service_date, datetime.time(10, 0))
        return TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=task,
            task_line=task_line,
            status=TaskScanLog.ScanStatus.OK,
            qty_base_delta=Decimal("1"),
            fp=fp,
            label_key=label_key,
            scan_snapshot_rev=0,
            posted_at=posted_at,
        )

    def _create_accrual(
        self,
        *,
        owner=None,
        rule=None,
        amount="10.00",
        service_date=None,
        bundle_key="",
        period=None,
        status=AccrualStatus.OPEN,
        fingerprint: str,
    ):
        owner = owner or self.owner
        rule = rule or self._create_rule(owner=owner)
        return BillingAccrual.objects.create(
            owner=owner,
            warehouse=self.warehouse,
            period=period,
            charge_type=rule.charge_type,
            rule=rule,
            service_date=service_date or datetime.date(2026, 3, 1),
            currency="CNY",
            quantity=Decimal("1"),
            unit_price=Decimal(amount),
            amount=Decimal(amount),
            tax_amount=Decimal("0.00"),
            status=status,
            bundle_key=bundle_key,
            acc_fingerprint=fingerprint,
            created_by=self.user,
        )

    def test_accrue_order_processing_creates_one_accrual_per_order(self):
        service_date = datetime.date(2026, 3, 1)
        self._create_rule(calc_method=CalcMethod.PER_ORDER, unit_price="10.00")

        task = self._create_task("TASK-ORDER-1")
        line1 = WmsTaskLine.objects.create(task=task)
        line2 = WmsTaskLine.objects.create(task=task)
        self._create_scan_log(task, line1, service_date, fp="fp-order-1", label_key="LBL-ORDER-1")
        self._create_scan_log(task, line2, service_date, fp="fp-order-2", label_key="LBL-ORDER-2")

        def resolver(task_line):
            return {"order_ids": {1000 + task_line.id}}

        with mock.patch("allapp.billing.services._load_taskline_order_resolver", return_value=resolver):
            events, accruals = accrue_order_processing_from_posted(
                self.owner.id,
                self.warehouse.id,
                service_date,
                service_date,
                by_user=self.user,
            )

        self.assertEqual(events, 2)
        self.assertEqual(accruals, 2)
        self.assertEqual(BillingAccrual.objects.count(), 2)
        self.assertEqual(
            BillingAccrual.objects.aggregate(total=models.Sum("amount"))["total"],
            Decimal("20.00"),
        )

    def test_accrue_order_processing_selects_rule_by_service_date(self):
        day1 = datetime.date(2026, 3, 1)
        day2 = datetime.date(2026, 3, 2)
        self._create_rule(calc_method=CalcMethod.PER_ORDER, unit_price="10.00", effective_to=day1)
        self._create_rule(calc_method=CalcMethod.PER_ORDER, unit_price="20.00", effective_from=day2)

        task = self._create_task("TASK-ORDER-2")
        line1 = WmsTaskLine.objects.create(task=task)
        line2 = WmsTaskLine.objects.create(task=task)
        self._create_scan_log(task, line1, day1, fp="fp-date-1", label_key="LBL-DATE-1")
        self._create_scan_log(task, line2, day2, fp="fp-date-2", label_key="LBL-DATE-2")

        def resolver(task_line):
            return {"order_ids": {2000 + task_line.id}}

        with mock.patch("allapp.billing.services._load_taskline_order_resolver", return_value=resolver):
            accrue_order_processing_from_posted(
                self.owner.id,
                self.warehouse.id,
                day1,
                day2,
                by_user=self.user,
            )

        amounts = {
            accrual.service_date: accrual.amount
            for accrual in BillingAccrual.objects.order_by("service_date")
        }
        self.assertEqual(amounts[day1], Decimal("10.00"))
        self.assertEqual(amounts[day2], Decimal("20.00"))

    def test_lock_period_uses_bundle_rule_scoped_to_owner_and_warehouse(self):
        service_date = datetime.date(2026, 3, 1)
        owner_rule = self._create_rule(
            owner=self.owner,
            bundle_key="BUNDLE-A",
            bundle_scope=BundleScope.PER_PERIOD,
            bundle_type=BundleType.CAP,
            bundle_price="100.00",
        )
        self._create_rule(
            owner=self.other_owner,
            bundle_key="BUNDLE-A",
            bundle_scope=BundleScope.PER_PERIOD,
            bundle_type=BundleType.CAP,
            bundle_price="1.00",
        )

        self._create_accrual(
            owner=self.owner,
            rule=owner_rule,
            amount="60.00",
            service_date=service_date,
            bundle_key="BUNDLE-A",
            fingerprint="acc-bundle-scope-1",
        )
        self._create_accrual(
            owner=self.owner,
            rule=owner_rule,
            amount="70.00",
            service_date=service_date + datetime.timedelta(days=1),
            bundle_key="BUNDLE-A",
            fingerprint="acc-bundle-scope-2",
        )

        period = lock_period(
            self.owner.id,
            self.warehouse.id,
            "2026-03-A",
            service_date,
            service_date + datetime.timedelta(days=1),
        )

        total = BillingAccrual.objects.filter(period=period).aggregate(total=models.Sum("amount"))["total"]
        self.assertEqual(period.status, PeriodStatus.CLOSED)
        self.assertEqual(total, Decimal("100.00"))

    def test_lock_period_fixed_bundle_hits_target_total(self):
        start_date = datetime.date(2026, 3, 10)
        fixed_rule = self._create_rule(
            bundle_key="FIXED-A",
            bundle_scope=BundleScope.PER_PERIOD,
            bundle_type=BundleType.FIXED,
            bundle_price="30.00",
        )

        self._create_accrual(
            rule=fixed_rule,
            amount="50.00",
            service_date=start_date,
            bundle_key="FIXED-A",
            fingerprint="acc-fixed-1",
        )
        self._create_accrual(
            rule=fixed_rule,
            amount="40.00",
            service_date=start_date + datetime.timedelta(days=1),
            bundle_key="FIXED-A",
            fingerprint="acc-fixed-2",
        )
        self._create_accrual(
            rule=fixed_rule,
            amount="10.00",
            service_date=start_date + datetime.timedelta(days=2),
            bundle_key="FIXED-A",
            fingerprint="acc-fixed-3",
        )

        period = lock_period(
            self.owner.id,
            self.warehouse.id,
            "2026-03-FIXED",
            start_date,
            start_date + datetime.timedelta(days=2),
        )

        accruals = list(BillingAccrual.objects.filter(period=period).order_by("service_date", "id"))
        self.assertEqual(sum((a.amount for a in accruals), Decimal("0.00")), Decimal("30.00"))
        self.assertTrue(all(a.amount >= 0 for a in accruals))

    def test_lock_period_rejects_non_open_period(self):
        BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-03-CLOSED",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 3, 31),
            status=PeriodStatus.CLOSED,
        )

        with self.assertRaises(ValueError):
            lock_period(
                self.owner.id,
                self.warehouse.id,
                "2026-03-CLOSED",
                datetime.date(2026, 3, 1),
                datetime.date(2026, 3, 31),
            )

    def test_generate_invoice_requires_closed_period_and_is_single_use(self):
        open_period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-03-OPEN",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 3, 31),
            status=PeriodStatus.OPEN,
        )
        rule = self._create_rule()
        self._create_accrual(
            rule=rule,
            amount="10.00",
            service_date=datetime.date(2026, 3, 1),
            period=open_period,
            status=AccrualStatus.LOCKED,
            fingerprint="acc-open-invoice",
        )

        with self.assertRaises(ValueError):
            generate_invoice_for_period(open_period, invoice_no="INV-OPEN")

        closed_period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-03-CINV",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 3, 31),
            status=PeriodStatus.CLOSED,
        )
        self._create_accrual(
            rule=rule,
            amount="15.00",
            service_date=datetime.date(2026, 3, 2),
            period=closed_period,
            status=AccrualStatus.LOCKED,
            fingerprint="acc-closed-invoice",
        )

        bill = generate_invoice_for_period(closed_period, invoice_no="INV-CLOSED")

        self.assertEqual(Bill.objects.filter(period=closed_period).count(), 1)
        self.assertEqual(bill.status, "ISSUED")
        with self.assertRaises(ValueError):
            generate_invoice_for_period(closed_period, invoice_no="INV-CLOSED-2")


class BillingMenuTests(TestCase):
    def test_profile_menu_points_billing_users_to_admin(self):
        warehouse = Warehouse.objects.create(code="WHM", name="Warehouse Menu")
        user = get_user_model().objects.create_user(
            username="billing-menu",
            password="x",
            warehouse=warehouse,
        )
        permission = Permission.objects.get(codename="view_bill")
        user.user_permissions.add(permission)

        request = APIRequestFactory().get("/api/auth/profile/")
        force_authenticate(request, user=user)
        response = profile_view(request)

        menus = response.data["menus"]
        self.assertIn(
            {"path": "/admin/billing/", "title": "计费", "icon": "el-icon-credit-card"},
            menus,
        )
