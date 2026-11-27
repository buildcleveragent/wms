import json
from datetime import date, timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import (
    InventoryCloseAttachment,
    InventoryClosePilot,
    InventoryCloseReview,
    InventoryDailyClose,
)
from allapp.locations.models import Warehouse

from .forms_daily_close import OpeningForm


class HistoricalOccupancyFormTests(SimpleTestCase):
    def test_explicit_holds_recompute_available_and_preserve_candidate(self):
        candidate = [{"allocated_qty": None, "onhand_qty": "12.0000"}]
        data = {
            "version": 1,
            "note": "已核对",
            "candidate_digest": "hash",
            "occupancy": "1 1.25 0 2",
            "complete": "on",
        }
        form = OpeningForm(data, rows=candidate)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.rows[0]["available_qty"], "8.7500")
        self.assertIsNone(candidate[0]["allocated_qty"])
        for value in ["", "1 _ 0 0", "1 13 0 0", "2 0 0 0", "1 0 1 0\n1 0 2 0"]:
            with self.subTest(value=value):
                invalid = OpeningForm(dict(data, occupancy=value), rows=candidate)
                self.assertFalse(invalid.is_valid())
                self.assertIn("occupancy", invalid.errors)


def route(name, *args):
    return reverse(f"console:inventory_daily_close_{name}", args=args)


def evidence_pdf(name):
    document = BytesIO()
    Image.new("RGB", (12, 12), color="white").save(document, format="PDF")
    return SimpleUploadedFile(name, document.getvalue(), "application/pdf")


class DailyCloseFixture:
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="DCO", name="日结甲货主")
        other_owner = Owner.objects.create(code="DCOTHER", name="日结乙货主")
        cls.warehouse = Warehouse.objects.create(code="DCW1", name="日结甲仓")
        cls.other_wh = Warehouse.objects.create(code="DCW2", name="日结乙仓")
        cls.binding = OwnerWarehouseBinding.objects.create(owner=cls.owner, warehouse=cls.warehouse)
        cls.day = date(2026, 1, 2)
        InventoryClosePilot.objects.create(
            warehouse=cls.warehouse, start_date=cls.day, enabled=True
        )
        cls.close = InventoryDailyClose.objects.create(
            owner=cls.owner,
            warehouse=cls.warehouse,
            business_date=cls.day,
            quality="MISSING",
            review_state="WAREHOUSE",
            issues=[{"issue": "snapshot_missing", "message": "缺少日终快照"}],
        )
        cls.other_close = InventoryDailyClose.objects.create(
            owner=other_owner,
            warehouse=cls.other_wh,
            business_date=cls.day,
            quality="APPROXIMATE",
            review_state="WAREHOUSE",
        )
        role = UserRoleScope.Role
        cls.manager = cls.account(
            "dc-manager", role.WAREHOUSE_MANAGER, "review", warehouse=cls.warehouse
        )
        cls.finance = cls.account(
            "dc-sign", role.WAREHOUSE_OPERATOR, "sign", warehouse=cls.warehouse
        )
        cls.owner_finance = cls.account("dc-owner", role.OWNER_FINANCE, "view", owner=cls.owner)
        cls.other_signer = cls.account(
            "dc-other", role.WAREHOUSE_MANAGER, "sign", warehouse=cls.other_wh
        )

    @staticmethod
    def account(name, role, capability, **scope):
        user = get_user_model().objects.create_user(username=name, **scope)
        UserRoleScope.objects.create(user=user, role=role, **scope)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inventory", codename=f"{capability}_inventorydailyclose"
            )
        )
        return user


class DailyCloseConsoleTests(DailyCloseFixture, TestCase):
    def test_scoped_list_filter_detail_and_manual_run(self):
        self.assertEqual(self.client.get(route("list")).status_code, 302)
        self.client.force_login(self.manager)
        response = self.client.get(route("list"))
        self.assertContains(response, "库存日结与差异复核")
        self.assertContains(response, route("detail", self.close.pk))
        self.assertNotContains(response, self.other_wh.name)
        self.assertNotContains(
            self.client.get(route("list"), {"quality": "TRUSTED"}), route("detail", self.close.pk)
        )
        self.assertNotContains(
            self.client.get(route("list"), {"warehouse": self.other_wh.pk}), self.other_wh.name
        )
        self.assertEqual(self.client.get(route("detail", self.other_close.pk)).status_code, 404)
        self.assertEqual(self.client.post(route("retry", self.other_close.pk)).status_code, 404)
        self.assertContains(self.client.get(route("detail", self.close.pk)), "缺少日终快照")
        self.assertEqual(self.client.get(route("retry", self.close.pk)).status_code, 405)
        result = self.client.post(
            route("run"),
            {"run-binding": self.binding.pk, "run-business_date": self.day - timedelta(days=1)},
        )
        header = InventoryDailyClose.objects.get(
            owner=self.owner, business_date=self.day - timedelta(days=1)
        )
        self.assertRedirects(result, route("detail", header.pk))
        self.assertEqual(self.client.post(route("pilot")).status_code, 403)

    def test_evidence_download_and_two_distinct_signers_through_web(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(route("action", self.close.pk, "submit")), "凭据附件")
        post = {
            "version": self.close.revision,
            "note": "内部仓管原件",
            "references": "外部凭证 PI-001",
            "files": evidence_pdf("daily.pdf"),
        }
        with TemporaryDirectory() as private_dir, self.settings(PRIVATE_CLOSE_ROOT=private_dir):
            response = self.client.post(route("action", self.close.pk, "submit"), post)
            self.assertRedirects(response, route("detail", self.close.pk))
            item = InventoryCloseAttachment.objects.get()
            self.assertEqual(self.client.get(route("attachment", item.pk)).status_code, 200)
            self.client.force_login(self.other_signer)
            self.assertEqual(self.client.get(route("attachment", item.pk)).status_code, 404)
            self.client.force_login(self.owner_finance)
            owner_page = self.client.get(route("detail", self.close.pk))
            self.assertContains(owner_page, "仓管提交复核")
            for hidden in ["内部仓管原件", "PI-001", "daily.pdf", "重新核查"]:
                self.assertNotContains(owner_page, hidden)
            self.assertEqual(self.client.get(route("attachment", item.pk)).status_code, 403)
            self.assertEqual(
                self.client.post(route("action", self.close.pk, "approve"), {}).status_code, 403
            )
            self.client.force_login(self.finance)
            self.assertContains(self.client.get(route("detail", self.close.pk)), "内部仓管原件")
            self.assertContains(
                self.client.get(route("action", self.close.pk, "approve")), "下载 daily.pdf"
            )
            self.close.refresh_from_db()
            result = self.client.post(
                route("action", self.close.pk, "approve"),
                {
                    "version": self.close.revision,
                    "note": "财务核对完成",
                    "billing_impact": "NO_CHANGE",
                },
            )
            self.assertRedirects(result, route("detail", self.close.pk))
            self.close.refresh_from_db()
            self.assertEqual(self.close.review_state, "CLOSED")

    def test_empty_opening_requires_full_attestation_and_distinct_finance_decision(self):
        self.client.force_login(self.manager)
        url = route("action", self.close.pk, "opening")
        preview = self.client.get(url)
        self.assertContains(preview, "期初核对明细")
        payload = {
            "version": self.close.revision,
            "note": "全仓结存原件齐全",
            "candidate_digest": preview.context["form"].initial["candidate_digest"],
            "references": "外部凭证 FULL-EMPTY",
        }
        self.assertEqual(self.client.post(url, payload).status_code, 400)
        with TemporaryDirectory() as private_dir, self.settings(PRIVATE_CLOSE_ROOT=private_dir):
            payload.update(
                complete="on",
                files=evidence_pdf("full.pdf"),
            )
            self.assertRedirects(self.client.post(url, payload), route("detail", self.close.pk))
            self.close.refresh_from_db()
            self.assertEqual(self.close.opening_versions.get().status, "PENDING")
            decision = route("action", self.close.pk, "approve-opening")
            self.assertEqual(self.client.get(decision).status_code, 403)
            self.client.force_login(self.finance)
            data = {"version": self.close.revision, "note": "计费数量一致"}
            self.assertEqual(self.client.post(decision, data).status_code, 400)
            data["billing_impact"] = "NO_CHANGE"
            result = self.client.post(decision, data)
            self.assertRedirects(result, route("detail", self.close.pk))
            self.assertEqual(self.close.opening_versions.get().status, "APPROVED")
            version_url = route("opening", self.close.pk, 1)
            self.assertContains(self.client.get(version_url), "期初版本 V1")
            self.client.force_login(self.other_signer)
            self.assertEqual(self.client.get(version_url).status_code, 404)
            self.close.refresh_from_db()
            self.assertEqual((self.close.quality, self.close.review_state), ("MISSING", "CLOSED"))

    def test_pilot_configuration_and_historical_recheck(self):
        root = get_user_model().objects.create_superuser(username="dc-root")
        self.client.force_login(root)
        response = self.client.post(
            route("pilot"),
            {
                "pilot-warehouse": self.other_wh.pk,
                "pilot-start_date": self.day,
                "pilot-enabled": "on",
            },
        )
        self.assertRedirects(response, route("list"))
        self.assertTrue(InventoryClosePilot.objects.get(warehouse=self.other_wh).enabled)
        InventoryCloseReview.objects.create(
            close=self.close,
            action="RECHECK",
            note=json.dumps({"原差异": [], "本次核查": self.close.issues}, ensure_ascii=False),
        )
        page = self.client.get(route("detail", self.close.pk))
        self.assertContains(page, "差异项从 0 项更新为 1 项")
        self.assertNotContains(page, route("action", self.close.pk, "approve"))
