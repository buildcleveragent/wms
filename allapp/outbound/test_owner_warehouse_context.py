from io import BytesIO, StringIO
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate
from openpyxl import Workbook

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.views import OutboundOrderViewSet, ProductViewSet, WarehouseViewSet
from allapp.products.models import Product, ProductUom


def outbound_submit_permission():
    return Permission.objects.get(
        content_type__app_label="outbound",
        codename="submit_outbound_as_owner_buyers",
    )


class OwnerWarehouseBindingTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Binding Owner", code="BINDOWN")
        self.warehouse = Warehouse.objects.create(code="BINDWH", name="Binding WH")

    def test_owner_and_warehouse_pair_is_unique(self):
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            OwnerWarehouseBinding.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
            )


class OwnerWarehouseOrderApiTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Order Owner", code="ORDEROWN")
        self.other_owner = Owner.objects.create(
            name="Other Order Owner",
            code="OTHEROWN",
        )
        self.warehouse = Warehouse.objects.create(code="ORDERWH", name="Order WH")
        self.unbound_warehouse = Warehouse.objects.create(
            code="UNBOUNDWH",
            name="Unbound WH",
        )
        self.inactive_warehouse = Warehouse.objects.create(
            code="INACTIVEWH",
            name="Inactive WH",
            is_active=False,
        )
        self.other_owner_warehouse = Warehouse.objects.create(
            code="OTHERWH",
            name="Other Owner WH",
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.inactive_warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.other_owner,
            warehouse=self.other_owner_warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.unbound_warehouse,
            is_active=False,
        )

        self.user = get_user_model().objects.create_user(
            username="owner-warehouse-sales",
            password="x",
            owner=self.owner,
        )
        self.user.user_permissions.add(outbound_submit_permission())
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="ORDER-CUSTOMER",
            name="Order Customer",
        )
        self.uom = ProductUom.objects.create(code="ORDER-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="ORDER-SKU",
            sku="ORDER-SKU",
            name="Order Product",
            base_uom=self.uom,
            price="10.00",
        )
        self.factory = APIRequestFactory()

    def _create(self, warehouse_id_marker=...):
        payload = {
            "customer_id": self.customer.id,
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "10.0000",
                }
            ],
        }
        if warehouse_id_marker is not ...:
            payload["warehouse_id"] = warehouse_id_marker
        request = self.factory.post(
            "/api/outbound/orders/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="owner-warehouse-context-0001",
        )
        force_authenticate(request, user=self.user)
        return OutboundOrderViewSet.as_view({"post": "create"})(request)

    def test_catalog_returns_only_active_authorized_warehouses(self):
        request = self.factory.get("/api/catalog/warehouses/")
        force_authenticate(request, user=self.user)

        response = WarehouseViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    "id": self.warehouse.id,
                    "code": self.warehouse.code,
                    "name": self.warehouse.name,
                }
            ],
        )

    def test_create_requires_explicit_warehouse(self):
        response = self._create()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(str(response.data["warehouse_id"][0]), "请选择出库仓库。")
        self.assertFalse(OutboundOrder.objects.exists())

    def test_create_rejects_inactive_or_other_owner_binding(self):
        for warehouse_id in (
            self.unbound_warehouse.id,
            self.inactive_warehouse.id,
            self.other_owner_warehouse.id,
            999999,
        ):
            with self.subTest(warehouse_id=warehouse_id):
                response = self._create(warehouse_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    str(response.data["warehouse_id"][0]),
                    "仓库不可用或未关联当前货主。",
                )
        self.assertFalse(OutboundOrder.objects.exists())

    def test_create_accepts_active_binding(self):
        response = self._create(self.warehouse.id)

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.owner_id, self.owner.id)
        self.assertEqual(order.warehouse_id, self.warehouse.id)

    def test_product_catalog_rejects_unbound_warehouse_filter(self):
        request = self.factory.get(
            "/api/catalog/products/",
            {"warehouse_id": self.other_owner_warehouse.id},
        )
        force_authenticate(request, user=self.user)

        response = ProductViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 403)

    def test_order_list_prefetches_lines_with_a_constant_query_budget(self):
        for index in range(20):
            order = OutboundOrder.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                customer=self.customer,
                src_bill_no=f"PERF-{index}",
                created_by=self.user,
                submit_status="SUBMITTED",
                approval_status="OWNER_PENDING",
            )
            OutboundOrderLine.objects.create(
                order=order,
                product=self.product,
                base_qty=Decimal("1.0000"),
                base_price=Decimal("10.0000"),
                created_by=self.user,
            )

        request = self.factory.get("/api/outbound/orders/", {"page_size": 100})
        force_authenticate(request, user=self.user)
        with CaptureQueriesContext(connection) as captured:
            response = OutboundOrderViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 20)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertLessEqual(
            len(captured),
            10,
            [query["sql"][:240] for query in captured],
        )


class DropShipImportWarehouseTests(TestCase):
    headers = (
        "订单编号",
        "收件人姓名",
        "收件人手机/电话",
        "收件人详细地址",
        "数量",
        "商家编码",
    )

    def setUp(self):
        self.owner = Owner.objects.create(name="Drop Ship Owner", code="DROP-OWN")
        self.warehouse = Warehouse.objects.create(code="DROP-WH", name="Drop Ship WH")
        self.unbound_warehouse = Warehouse.objects.create(
            code="DROP-WH-2",
            name="Unbound Drop Ship WH",
        )
        self.inactive_warehouse = Warehouse.objects.create(
            code="DROP-WH-3",
            name="Inactive Drop Ship WH",
            is_active=False,
        )
        self.inactive_binding_warehouse = Warehouse.objects.create(
            code="DROP-WH-4",
            name="Inactive Binding Drop Ship WH",
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.inactive_warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.inactive_binding_warehouse,
            is_active=False,
        )
        self.user = get_user_model().objects.create_user(
            username="drop-ship-sales",
            password="x",
            owner=self.owner,
        )
        self.user.user_permissions.add(outbound_submit_permission())
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CASH",
            name="散客",
        )
        uom = ProductUom.objects.create(code="DROP-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="DROP-PRODUCT",
            name="Drop Ship Product",
            base_uom=uom,
            price="12.00",
        )
        self.factory = APIRequestFactory()

    def _excel_file(self, filename="drop-ship.xlsx", rows=None):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(self.headers)
        for row in rows or [
            (
                "DROP-ORDER-001",
                "张三",
                "13800138000",
                "测试路1号",
                "2",
                self.product.code,
            )
        ]:
            sheet.append(row)
        output = BytesIO()
        workbook.save(output)
        return SimpleUploadedFile(
            filename,
            output.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

    def _import(self, *, filename="drop-ship.xlsx", warehouse_marker=...):
        data = {"file": self._excel_file(filename)}
        if warehouse_marker is not ...:
            data["warehouse_id"] = warehouse_marker
        request = self.factory.post(
            "/api/outbound/orders/import-drop-ship-excel/",
            data,
            format="multipart",
        )
        force_authenticate(request, user=self.user)
        return OutboundOrderViewSet.as_view({"post": "import_drop_ship_excel"})(request)

    def test_import_requires_explicit_warehouse(self):
        response = self._import()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["warehouse_id"], "请选择出库仓库。")
        self.assertFalse(OutboundOrder.objects.exists())

    def test_import_rejects_invalid_warehouse_value(self):
        response = self._import(warehouse_marker="not-an-id")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["warehouse_id"], "请选择出库仓库。")
        self.assertFalse(OutboundOrder.objects.exists())

    def test_import_rejects_unavailable_warehouses(self):
        for warehouse_id in (
            self.unbound_warehouse.id,
            self.inactive_warehouse.id,
            self.inactive_binding_warehouse.id,
            999999,
        ):
            with self.subTest(warehouse_id=warehouse_id):
                response = self._import(warehouse_marker=warehouse_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.data["warehouse_id"],
                    "仓库不可用或未关联当前货主。",
                )
        self.assertFalse(OutboundOrder.objects.exists())

    def test_import_rejects_legacy_xls_extension(self):
        response = self._import(
            filename="drop-ship.xls",
            warehouse_marker=self.warehouse.id,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(".xlsx", response.data["detail"])
        self.assertFalse(OutboundOrder.objects.exists())

    def test_import_passes_warehouse_to_each_row_order(self):
        response = self._import(warehouse_marker=self.warehouse.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_rows"], 1)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["fail_count"], 0)
        order = OutboundOrder.objects.get(src_bill_no="DROP-ORDER-001")
        self.assertEqual(order.owner_id, self.owner.id)
        self.assertEqual(order.warehouse_id, self.warehouse.id)

    def test_import_preserves_partial_success_and_duplicate_counts(self):
        cash_customer = Customer.objects.get(owner=self.owner, code="CASH")
        OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=cash_customer,
            src_bill_no="DROP-ORDER-DUPLICATE",
        )
        rows = [
            (
                "DROP-ORDER-SUCCESS",
                "张三",
                "13800138000",
                "测试路1号",
                "2",
                self.product.code,
            ),
            (
                "DROP-ORDER-FAIL",
                "李四",
                "13800138001",
                "测试路2号",
                "1",
                "SKU-NOT-FOUND",
            ),
            (
                "DROP-ORDER-DUPLICATE",
                "王五",
                "13800138002",
                "测试路3号",
                "1",
                self.product.code,
            ),
        ]
        data = {
            "file": self._excel_file(rows=rows),
            "warehouse_id": self.warehouse.id,
        }
        request = self.factory.post(
            "/api/outbound/orders/import-drop-ship-excel/",
            data,
            format="multipart",
        )
        force_authenticate(request, user=self.user)

        response = OutboundOrderViewSet.as_view({"post": "import_drop_ship_excel"})(
            request
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total_rows"], 3)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["fail_count"], 1)
        self.assertEqual(response.data["skip_count"], 1)
        self.assertEqual(len(response.data["successes"]), 1)
        self.assertEqual(len(response.data["errors"]), 1)
        self.assertEqual(len(response.data["skips"]), 1)
        created = OutboundOrder.objects.get(src_bill_no="DROP-ORDER-SUCCESS")
        self.assertEqual(created.warehouse_id, self.warehouse.id)
        self.assertFalse(
            OutboundOrder.objects.filter(src_bill_no="DROP-ORDER-FAIL").exists()
        )


class SeedOwnerWarehouseBindingsCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Seed Owner", code="SEEDOWN")
        self.warehouse = Warehouse.objects.create(code="SEEDWH", name="Seed WH")
        self.user = get_user_model().objects.create_user(
            username="seed-owner-warehouse",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="SEED-CUSTOMER",
            name="Seed Customer",
        )
        OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=customer,
        )

    def test_dry_run_does_not_write_and_reports_all_sources(self):
        output = StringIO()

        call_command("seed_owner_warehouse_bindings", "--dry-run", stdout=output)

        self.assertFalse(OwnerWarehouseBinding.objects.exists())
        text = output.getvalue()
        self.assertIn("legacy_user", text)
        self.assertIn("outbound_order", text)
        self.assertIn("mode=dry-run candidates=1", text)

    def test_apply_is_idempotent_and_reactivates_existing_binding(self):
        first_output = StringIO()
        call_command("seed_owner_warehouse_bindings", "--apply", stdout=first_output)
        binding = OwnerWarehouseBinding.objects.get(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.assertIn("created=1", first_output.getvalue())

        binding.is_active = False
        binding.save(update_fields=("is_active", "updated_at"))
        second_output = StringIO()
        call_command("seed_owner_warehouse_bindings", "--apply", stdout=second_output)

        binding.refresh_from_db()
        self.assertTrue(binding.is_active)
        self.assertIn("reactivated=1", second_output.getvalue())
        self.assertEqual(
            OwnerWarehouseBinding.all_objects.filter(
                owner=self.owner,
                warehouse=self.warehouse,
            ).count(),
            1,
        )
