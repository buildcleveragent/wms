from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from allapp.inventory import test_owner_exports as fixtures
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder
from allapp.outbound.order_filters import filter_orders
from allapp.outbound.serializers import OutboundOrderReadSerializer
from allapp.outbound.views import OutboundOrderViewSet


class OwnerAccessAndAmountRegressionTests(TestCase):
    setUpTestData = classmethod(fixtures.OwnerExportTests.setUpTestData.__func__)
    setUp = fixtures.OwnerExportTests.setUp
    call = fixtures.OwnerExportTests.call
    workbook = fixtures.OwnerExportTests.workbook

    def test_permission_revocation_denies_every_inventory_endpoint_and_profile(self):
        client = APIClient()
        user = get_user_model().objects.get(pk=self.user.pk)
        client.force_authenticate(user)
        self.assertTrue(
            client.get("/api/auth/profile/").data["capabilities"]["can_view_owner_inventory"]
        )
        user.user_permissions.remove(Permission.objects.get(codename="access_owner_sales"))
        client.force_authenticate(get_user_model().objects.get(pk=user.pk))
        paths = [
            "/api/inventory/summary/",
            "/api/inventory/summary/export/",
            f"/api/inventory/summary/{self.summary.pk}/detail/",
            f"/api/inventory/summary/{self.summary.pk}/transactions/",
            f"/api/inventory/summary/{self.summary.pk}/detail/export/",
            "/api/inventory/owner-alerts/",
            "/api/inventory/owner-alerts/items/?category=low",
            "/api/inventory/owner-alerts/settings/",
            "/api/inventory/owner-alerts/products/",
            f"/api/inventory/owner-alerts/products/{self.product.pk}/settings/",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)
        caps = client.get("/api/auth/profile/").data["capabilities"]
        self.assertFalse(caps["can_view_owner_inventory"])
        self.assertFalse(caps["can_export_owner_inventory"])
        self.assertFalse(caps["can_manage_owner_inventory_alerts"])
        self.assertFalse(InventoryTransaction.objects.filter(owner=self.owner).exists())

    def test_line_rounding_matches_header_freeze_excel_and_chinese_status(self):
        self.line.base_qty = Decimal("1")
        self.line.base_price = Decimal("19.125")
        self.line.save()
        self.order.lines.create(product=self.product, base_qty=1, base_price=Decimal("11.25"))
        order = OutboundOrderViewSet._optimized_order_queryset().get(pk=self.order.pk)
        with CaptureQueriesContext(connection) as queries:
            data = OutboundOrderReadSerializer(order).data
        self.assertEqual(data["total_amount"], Decimal("30.37"))
        self.assertEqual(sum(line["amount"] for line in data["lines"]), Decimal("30.37"))
        self.assertEqual(data["submit_status_name"], order.get_submit_status_display())
        self.assertLessEqual(len(queries), 1)
        self.assertEqual(self.order._calculate_final_order_amount(), Decimal("30.37"))
        sheet = self.workbook(self.call())["订单商品明细"]
        self.assertEqual(
            sum(Decimal(str(sheet.cell(row, 17).value)) for row in (2, 3)), Decimal("30.37")
        )
        order.approval_status = "UNKNOWN"
        self.assertEqual(
            OutboundOrderReadSerializer(order).data["approval_status_name"], "状态待核对"
        )

    def test_warehouse_approval_label_does_not_claim_picking_is_pending(self):
        self.order.approval_status = "WHS_APPROVED"
        self.order.is_closed = True
        self.order.close_reason = "已完成发运"
        self.order.save(update_fields=["approval_status", "is_closed", "close_reason"])

        data = OutboundOrderReadSerializer(self.order).data
        self.assertEqual(data["approval_status_name"], "仓库管理员已确认")
        sheet = self.workbook(self.call())["订单商品明细"]
        headers = [cell.value for cell in sheet[1]]
        status = sheet.cell(2, headers.index("审核状态") + 1).value
        self.assertEqual(status, data["approval_status_name"])

    def test_product_search_matches_order_then_exports_all_its_lines_without_fanout(self):
        from allapp.products.models import Product

        other = Product.objects.create(
            owner=self.owner,
            code="ANOTHER",
            name="不同商品",
            base_uom=self.unit,
        )
        self.order.lines.create(product=other, base_qty=1, base_price=2)
        qs = filter_orders(OutboundOrder.objects.all(), {"search": self.product.name})
        sql = str(qs.query)
        self.assertIn("EXISTS", sql)
        self.assertNotIn("JOIN `outbound_outboundorderline`", sql)
        self.assertEqual(list(qs.values_list("id", flat=True)), [self.order.pk])
        self.assertEqual(
            self.workbook(self.call(params={"search": self.product.name}))["订单商品明细"].max_row,
            3,
        )
