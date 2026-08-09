from decimal import Decimal
from unittest import mock
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.core.models import PrintConfig
from allapp.inventory.models import (
    InventoryDetail,
    InventoryTransaction,
    PostingJournal,
)
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound import services as outbound_services
from allapp.outbound.export_print import pick_task_print
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import (
    AssistedOutboundOrderViewSet,
    OutboundOrderViewSet,
    PickTaskViewSet,
)
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import Product, ProductBarcode, ProductPackage, ProductUom
from allapp.tasking.models import TaskScanLog, TaskStatusLog, WmsTask


def _permission(app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class AssistedOutboundFlowTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = Owner.objects.create(
            code="AFW-OWN",
            name="Assisted flow owner",
            allow_warehouse_assisted_outbound=True,
        )
        self.warehouse = Warehouse.objects.create(
            code="AFW-WH",
            name="Assisted flow warehouse",
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="AFW",
            name="Assisted flow subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="AFW-01-01-01",
            name="Assisted flow location",
        )
        self.operator = get_user_model().objects.create_user(
            username="assisted-flow-operator",
            password="x",
            warehouse=self.warehouse,
        )
        self.operator.user_permissions.add(
            _permission("outbound", "process_warehouse_assisted_outbound"),
            _permission("tasking", "claim_task_as_wh_operator"),
        )
        UserRoleScope.objects.create(
            user=self.operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.operator,
            code="ASSIST-FLOW-CUSTOMER",
            name="Assisted flow customer",
        )
        self.uom = ProductUom.objects.create(
            code="ASSIST-FLOW-EA",
            name="EA",
            is_active=True,
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="ASSIST-FLOW-SKU",
            sku="ASSIST-FLOW-SKU",
            name="Assisted flow product",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("12.50"),
        )
        self.detail = InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
        )

    def _request(self, method, path, data=None, *, user=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=user or self.operator)
        return request

    def _payload(self, *, request_id=None, qty="2.000"):
        return {
            "request_id": str(request_id or uuid4()),
            "owner_id": self.owner.id,
            "customer_id": self.customer.id,
            "src_bill_no": "ASSIST-SOURCE-1",
            "delivery_method": "COURIER",
            "contact": "收件人",
            "contact_phone": "13800000000",
            "ship_to": "测试地址",
            "remark": "代办测试",
            "assistance_reason": "货主未使用系统",
            "items": [{"product_id": self.product.id, "qty": qty}],
        }

    def _create(self, payload):
        view = AssistedOutboundOrderViewSet.as_view({"post": "create"})
        return view(self._request("post", "/api/outbound/assisted-orders/", payload))

    def test_create_approves_allocates_and_releases_with_real_actor(self):
        response = self._create(self._payload())

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["order_id"])
        task = WmsTask.objects.get(pk=response.data["task_id"])
        self.detail.refresh_from_db()

        self.assertEqual(order.processing_mode, "WAREHOUSE_ASSISTED")
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "WHS_APPROVED")
        self.assertEqual(order.assisted_by_id, self.operator.id)
        self.assertEqual(order.approved_by_ownermanager_id, self.operator.id)
        self.assertEqual(order.approved_by_warehouse_id, self.operator.id)
        self.assertIsNotNone(order.approved_at_ownermanager)
        self.assertIsNotNone(order.approved_at_warehouse)
        self.assertEqual(task.status, WmsTask.Status.RELEASED)
        self.assertIsNotNone(task.released_at)
        self.assertEqual(task.updated_by_id, self.operator.id)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(task.lines.get().status, task.lines.model.Status.RELEASED)
        self.assertEqual(self.detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(
            TaskStatusLog.objects.get(task=task).changed_by_id,
            self.operator.id,
        )

        own_print_request = self.factory.get(
            f"/api/outbound/pda/pick-tasks/{task.id}/print/"
        )
        own_print_request.user = self.operator
        self.assertEqual(pick_task_print(own_print_request, task.id).status_code, 200)

        other_warehouse = Warehouse.objects.create(
            code="AFW-PRT-O", name="Print other warehouse"
        )
        cross_warehouse_user = get_user_model().objects.create_user(
            username="assisted-flow-print-cross", warehouse=other_warehouse
        )
        cross_warehouse_user.user_permissions.add(
            _permission("tasking", "view_wmstask")
        )
        UserRoleScope.objects.create(
            user=cross_warehouse_user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=other_warehouse,
        )
        denied_print_request = self.factory.get(
            f"/api/outbound/pda/pick-tasks/{task.id}/print/"
        )
        denied_print_request.user = cross_warehouse_user
        self.assertEqual(
            pick_task_print(denied_print_request, task.id).status_code, 404
        )

    def test_request_id_same_payload_replays_and_changed_payload_conflicts(self):
        request_id = uuid4()
        payload = self._payload(request_id=request_id)
        first = self._create(payload)
        replay = self._create(payload)
        changed = {**payload, "ship_to": "另一个地址"}
        conflict = self._create(changed)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        self.assertTrue(replay.data["replayed"])
        self.assertEqual(replay.data["order_id"], first.data["order_id"])
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(
            OutboundOrder.objects.filter(assistance_request_id=request_id).count(),
            1,
        )

    def test_request_id_rejects_changed_supplied_price(self):
        request_id = uuid4()
        payload = self._payload(request_id=request_id)
        payload["items"][0]["price"] = "8.5000"
        first = self._create(payload)
        changed = {
            **payload,
            "items": [
                {"product_id": self.product.id, "qty": "2.000", "price": "9.5000"}
            ],
        }

        conflict = self._create(changed)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(conflict.status_code, 409, conflict.data)

    def test_shortage_rolls_back_order_allocation_and_task(self):
        before_orders = OutboundOrder.objects.count()
        before_tasks = WmsTask.objects.count()

        response = self._create(self._payload(qty="11.000"))

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("库存不足", str(response.data))
        self.assertEqual(OutboundOrder.objects.count(), before_orders)
        self.assertEqual(WmsTask.objects.count(), before_tasks)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, Decimal("0.0000"))

    def test_strict_entry_rejects_missing_permission_and_owner_opt_out(self):
        unauthorized = get_user_model().objects.create_user(
            username="assisted-flow-no-permission",
            password="x",
            warehouse=self.warehouse,
        )
        view = AssistedOutboundOrderViewSet.as_view({"post": "create"})
        denied = view(
            self._request(
                "post",
                "/api/outbound/assisted-orders/",
                self._payload(),
                user=unauthorized,
            )
        )
        self.assertEqual(denied.status_code, 403, denied.data)

        self.owner.allow_warehouse_assisted_outbound = False
        self.owner.save(update_fields=["allow_warehouse_assisted_outbound"])
        opted_out = self._create(self._payload())
        self.assertEqual(opted_out.status_code, 400, opted_out.data)
        self.owner.allow_warehouse_assisted_outbound = True
        self.owner.save(update_fields=["allow_warehouse_assisted_outbound"])

    def test_missing_price_is_allowed_and_supplied_price_is_used(self):
        self.product.price = None
        self.product.save(update_fields=["price"])

        without_price = self._create(self._payload())
        self.assertEqual(without_price.status_code, 201, without_price.data)
        order = OutboundOrder.objects.get(pk=without_price.data["order_id"])
        self.assertEqual(order.lines.get().base_price, Decimal("0.0000"))

        payload = self._payload()
        payload["request_id"] = str(uuid4())
        payload["src_bill_no"] = "ASSIST-SOURCE-WITH-PRICE"
        payload["items"][0]["price"] = "9.8765"
        with_price = self._create(payload)
        self.assertEqual(with_price.status_code, 201, with_price.data)
        priced_order = OutboundOrder.objects.get(pk=with_price.data["order_id"])
        self.assertEqual(priced_order.lines.get().base_price, Decimal("9.8765"))

    def test_one_assisted_order_accepts_multiple_products(self):
        second_product = Product.objects.create(
            owner=self.owner,
            code="ASSIST-FLOW-SKU-2",
            sku="ASSIST-FLOW-SKU-2",
            name="Assisted flow product 2",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=None,
        )
        second_detail = InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=second_product,
            location=self.location,
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
        )
        payload = self._payload(qty="2.000")
        payload["items"].append(
            {"product_id": second_product.id, "qty": "3.000", "price": "6.5000"}
        )

        response = self._create(payload)

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["order_id"])
        task = WmsTask.objects.get(pk=response.data["task_id"])
        self.assertEqual(order.lines.count(), 2)
        self.assertEqual(task.lines.values("product_id").distinct().count(), 2)
        self.detail.refresh_from_db()
        second_detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(second_detail.allocated_qty, Decimal("3.0000"))

    def test_package_catalog_and_server_validated_base_quantity_conversion(self):
        add_product_barcode(
            product=self.product,
            barcode="6901234567890",
            barcode_type=ProductBarcode.BarcodeType.GTIN,
            is_primary=True,
        )
        add_product_barcode(
            product=self.product,
            barcode="ASSIST-FLOW-UNIT-BARCODE",
            barcode_type=ProductBarcode.BarcodeType.UNIT,
            is_primary=True,
        )
        self.product.refresh_from_db()
        carton_uom = ProductUom.objects.create(
            code="ASSIST-FLOW-CTN",
            name="箱",
            is_active=True,
        )
        package = ProductPackage.objects.create(
            product=self.product,
            uom=carton_uom,
            qty_in_base=4,
            barcode="ASSIST-FLOW-CTN-BARCODE",
            is_sales_default=True,
        )

        catalog_view = AssistedOutboundOrderViewSet.as_view({"get": "products"})
        blank_catalog = catalog_view(
            self._request(
                "get",
                f"/api/outbound/assisted-orders/products/?owner_id={self.owner.id}",
            )
        )
        self.assertEqual(blank_catalog.status_code, 200, blank_catalog.data)
        self.assertEqual(blank_catalog.data, [])
        whitespace_catalog = catalog_view(
            self._request(
                "get",
                (
                    "/api/outbound/assisted-orders/products/"
                    f"?owner_id={self.owner.id}&search=%20%20"
                ),
            )
        )
        self.assertEqual(whitespace_catalog.status_code, 200, whitespace_catalog.data)
        self.assertEqual(whitespace_catalog.data, [])

        catalog = catalog_view(
            self._request(
                "get",
                (
                    "/api/outbound/assisted-orders/products/"
                    f"?owner_id={self.owner.id}&search={self.product.sku}"
                ),
            )
        )

        self.assertEqual(catalog.status_code, 200, catalog.data)
        product_data = next(row for row in catalog.data if row["id"] == self.product.id)
        self.assertEqual(product_data["gtin"], self.product.gtin)
        package_option = next(
            option
            for option in product_data["unitOptions"]
            if option["package_id"] == package.id
        )
        self.assertEqual(package_option["label"], "箱")
        self.assertEqual(package_option["multiplier"], 4)
        self.assertEqual(
            product_data["unitOptions"][product_data["selectedUnitIndex"]][
                "package_id"
            ],
            package.id,
        )

        for search_term in (
            self.product.name,
            self.product.code,
            self.product.gtin,
            self.product.unit_barcode,
            package.barcode,
        ):
            searched = catalog_view(
                self._request(
                    "get",
                    (
                        "/api/outbound/assisted-orders/products/?"
                        + urlencode({"owner_id": self.owner.id, "search": search_term})
                    ),
                )
            )
            self.assertEqual(searched.status_code, 200, searched.data)
            self.assertEqual(
                [row["id"] for row in searched.data],
                [self.product.id],
            )

        payload = self._payload(qty="8.000")
        payload["contact"] = "实际收件人张三"
        payload["items"][0].update(
            {
                "package_id": package.id,
                "package_qty": "2.000",
            }
        )
        created = self._create(payload)

        self.assertEqual(created.status_code, 201, created.data)
        line = OutboundOrder.objects.get(pk=created.data["order_id"]).lines.get()
        self.assertEqual(line.base_qty, Decimal("8.000"))
        self.assertEqual(line.aux_uom_id, package.id)
        self.assertEqual(line.aux_qty, Decimal("2.000"))
        self.assertEqual(line.ratio, Decimal("4.0000"))
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, Decimal("8.0000"))

        print_request = self.factory.get(
            f"/api/pda/pick-tasks/{created.data['task_id']}/print/"
        )
        print_request.user = self.operator
        PrintConfig.objects.create(
            code="outbound_assisted_test",
            name="代办出库打印测试",
            module=PrintConfig.Module.OUTBOUND,
            print_method=PrintConfig.PrintMethod.BACKEND_HTML,
            printer_type=PrintConfig.PrinterType.DOT_MATRIX,
            page_size_css="8in 4in",
            page_margin="1mm 2mm",
            sheet_width="95%",
            sheet_padding_top="1mm",
            sheet_padding_right="2mm",
            sheet_padding_bottom="3mm",
            sheet_padding_left="4mm",
            font_family="Noto Sans SC, Arial, sans-serif",
            body_font_size="11px",
            company_font_size="21px",
            title_font_size="19px",
            meta_font_size="12px",
            table_font_size="10px",
            table_header_font_size="9px",
            money_font_size="14px",
            footer_font_size="8px",
            body_line_height="1.3",
            meta_line_height="1.2",
            table_line_height="16px",
            money_line_height="17px",
            footer_line_height="1.1",
            table_cell_padding="2px 3px",
            is_default=True,
            is_active=True,
        )
        print_response = pick_task_print(print_request, created.data["task_id"])
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(print_response, "size: 8in 4in")
        self.assertNotContains(print_response, "size: A4")
        self.assertContains(
            print_response,
            "font-family: Noto Sans SC, Arial, sans-serif",
        )
        self.assertContains(print_response, "font-size: 21px")
        self.assertContains(print_response, "font-size: 19px")
        self.assertContains(print_response, "font-size: 14px")
        self.assertContains(print_response, "padding: 2px 3px")
        self.assertContains(print_response, "箱")
        self.assertContains(print_response, "1 箱=4")
        self.assertContains(print_response, "实际收件人张三")
        self.assertNotContains(print_response, self.customer.name)

        replayed = self._create(payload)
        self.assertEqual(replayed.status_code, 200, replayed.data)
        self.assertTrue(replayed.data["idempotent"])

        changed_to_base_unit = {
            **payload,
            "items": [{"product_id": self.product.id, "qty": "8.000"}],
        }
        conflict = self._create(changed_to_base_unit)
        self.assertEqual(conflict.status_code, 409, conflict.data)

    def test_rejects_inconsistent_package_conversion(self):
        carton_uom = ProductUom.objects.create(
            code="ASSIST-FLOW-BAD-CTN",
            name="大箱",
            is_active=True,
        )
        package = ProductPackage.objects.create(
            product=self.product,
            uom=carton_uom,
            qty_in_base=5,
        )
        payload = self._payload(qty="9.000")
        payload["items"][0].update(
            {
                "package_id": package.id,
                "package_qty": "2.000",
            }
        )

        response = self._create(payload)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("包装数量换算不一致", str(response.data))
        self.assertFalse(
            OutboundOrder.objects.filter(
                assistance_request_id=payload["request_id"]
            ).exists()
        )

    def test_rejects_cross_owner_customer_and_cash_without_recipient_fields(self):
        other_owner = Owner.objects.create(
            code="AFW-OTH",
            name="Other owner",
            allow_warehouse_assisted_outbound=True,
        )
        other_customer = Customer.objects.create(
            owner=other_owner,
            salesperson=self.operator,
            code="ASSIST-FLOW-OTHER-C",
            name="Other customer",
        )
        cross_owner = self._payload()
        cross_owner["customer_id"] = other_customer.id
        response = self._create(cross_owner)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("不属于所选货主", str(response.data))

        cash = Customer.objects.create(
            owner=self.owner,
            salesperson=self.operator,
            code="CASH",
            name="Cash customer",
        )
        missing_recipient = self._payload()
        missing_recipient.update(
            {
                "request_id": str(uuid4()),
                "customer_id": cash.id,
                "src_bill_no": "ASSIST-CASH-1",
                "contact": "",
                "contact_phone": "",
                "ship_to": "",
            }
        )
        response = self._create(missing_recipient)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("收件人", str(response.data))
        self.assertEqual(OutboundOrder.objects.count(), 0)

    def test_same_operator_can_submit_review_post_close_and_retry(self):
        created = self._create(self._payload())
        self.assertEqual(created.status_code, 201, created.data)
        order = OutboundOrder.objects.get(pk=created.data["order_id"])
        task = WmsTask.objects.get(pk=created.data["task_id"])
        line = task.lines.get()
        line.qty_done = line.qty_plan
        line.save(update_fields=["qty_done"])
        TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=task,
            task_line=line,
            product=self.product,
            location=self.location,
            by_user=self.operator,
            barcode=self.product.sku,
            code_type="SKU",
            qty_base_delta=line.qty_plan,
            fp=f"assisted-flow-{task.id}",
            scan_snapshot_rev=0,
        )

        submit_view = PickTaskViewSet.as_view({"post": "create_review_task"})
        submitted = submit_view(
            self._request(
                "post",
                f"/api/pda/pick-tasks/{task.id}/create-review-task/",
            ),
            pk=task.id,
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)
        task.refresh_from_db()
        self.assertEqual(task.status, WmsTask.Status.COMPLETED)
        self.assertEqual(task.review_status, WmsTask.ReviewStatus.PENDING)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.NOT_READY)
        self.assertEqual(task.picked_by_id, self.operator.id)

        reviewer = get_user_model().objects.create_user(
            username="assisted-flow-review-queue", warehouse=self.warehouse
        )
        reviewer.user_permissions.add(
            _permission("tasking", "view_wmstask"),
            _permission("tasking", "taskconfirm_as_wh_manager"),
        )
        UserRoleScope.objects.create(
            user=reviewer,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        list_view = PickTaskViewSet.as_view({"get": "list"})
        review_queue = list_view(
            self._request(
                "get",
                "/api/pda/pick-tasks/?for_review=1",
                user=reviewer,
            )
        )
        queue_rows = (
            review_queue.data.get("results", review_queue.data)
            if isinstance(review_queue.data, dict)
            else review_queue.data
        )
        self.assertIn(task.id, {row["id"] for row in queue_rows})
        own_queue = list_view(self._request("get", "/api/pda/pick-tasks/?for_review=1"))
        own_rows = (
            own_queue.data.get("results", own_queue.data)
            if isinstance(own_queue.data, dict)
            else own_queue.data
        )
        self.assertNotIn(task.id, {row["id"] for row in own_rows})

        post_view = PickTaskViewSet.as_view({"post": "post"})
        posted = post_view(
            self._request("post", f"/api/pda/pick-tasks/{task.id}/post/"),
            pk=task.id,
        )
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertFalse(posted.data["idempotent"])

        task.refresh_from_db()
        order.refresh_from_db()
        self.detail.refresh_from_db()
        self.assertEqual(task.review_status, WmsTask.ReviewStatus.APPROVED)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertEqual(task.approved_by_id, self.operator.id)
        self.assertEqual(task.posted_by_id, self.operator.id)
        self.assertTrue(order.is_closed)
        self.assertEqual(order.close_reason, "仓库代办出库完成")
        self.assertEqual(order.approval_status, "WHS_APPROVED")
        self.assertEqual(self.detail.onhand_qty, Decimal("8.0000"))
        self.assertEqual(self.detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.id,
                tx_type="ISSUE",
            ).count(),
            1,
        )

        approved_by_id = task.approved_by_id
        posted_by_id = task.posted_by_id
        WmsTask.objects.filter(pk=task.id).update(
            posting_status=WmsTask.PostingStatus.PENDING,
            posted_at=None,
        )
        replay = post_view(
            self._request("post", f"/api/pda/pick-tasks/{task.id}/post/"),
            pk=task.id,
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        task.refresh_from_db()
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertIsNotNone(task.posted_at)
        self.assertEqual(task.approved_by_id, approved_by_id)
        self.assertEqual(task.posted_by_id, posted_by_id)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.id,
                tx_type="ISSUE",
            ).count(),
            1,
        )

    def test_approved_failed_task_resumes_without_overwriting_reviewer(self):
        created = self._create(self._payload())
        self.assertEqual(created.status_code, 201, created.data)
        task = WmsTask.objects.get(pk=created.data["task_id"])
        original_reviewer = get_user_model().objects.create_user(
            username="assisted-flow-original-reviewer",
            warehouse=self.warehouse,
        )
        line = task.lines.get()
        line.qty_done = line.qty_plan
        line.save(update_fields=["qty_done"])
        review_task = outbound_services.create_review_task_for_pick(
            task,
            by_user=self.operator,
        )
        WmsTask.objects.filter(pk=review_task.pk).update(
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
            approved_by=original_reviewer,
            approved_at=timezone.now(),
        )
        WmsTask.objects.filter(pk=task.id).update(
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
            approved_by=original_reviewer,
            approved_at=timezone.now(),
        )

        post_view = PickTaskViewSet.as_view({"post": "post"})
        with mock.patch(
            "allapp.tasking.plugins.handlers.DefaultPostingHandler._handle_atomic",
            side_effect=ValueError("simulated posting failure"),
        ):
            with self.assertRaisesRegex(ValueError, "simulated posting failure"):
                post_view(
                    self._request("post", f"/api/pda/pick-tasks/{task.id}/post/"),
                    pk=task.id,
                )

        task.refresh_from_db()
        self.assertEqual(task.review_status, WmsTask.ReviewStatus.APPROVED)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.FAILED)
        self.assertEqual(task.approved_by_id, original_reviewer.id)
        self.assertEqual(task.posted_by_id, self.operator.id)
        self.assertEqual(
            PostingJournal.objects.get(
                src_model="WmsTask", src_id=task.id, tx_type="POST"
            ).status,
            "FAILED",
        )

        def mark_posted(*, task_id, by_user, note):
            self.assertEqual(by_user.id, self.operator.id)
            PostingJournal.objects.update_or_create(
                src_model="WmsTask",
                src_id=task_id,
                tx_type="POST",
                defaults={"status": "POSTED", "message": "OK: resumed"},
            )
            WmsTask.objects.filter(pk=task_id).update(
                posting_status=WmsTask.PostingStatus.POSTED,
                posted_by=by_user,
                posted_at=timezone.now(),
            )
            return {"ok": True, "tx_created": 1}

        with mock.patch(
            "allapp.outbound.views._run_posting_handler",
            side_effect=mark_posted,
        ), mock.patch(
            "allapp.outbound.views.outbound_services.close_assisted_order_for_posted_task"
        ):
            response = post_view(
                self._request("post", f"/api/pda/pick-tasks/{task.id}/post/"),
                pk=task.id,
            )

        self.assertEqual(response.status_code, 200, response.data)
        task.refresh_from_db()
        self.assertEqual(task.approved_by_id, original_reviewer.id)
        self.assertEqual(task.posted_by_id, self.operator.id)
        self.assertEqual(
            PostingJournal.objects.get(
                src_model="WmsTask", src_id=task.id, tx_type="POST"
            ).status,
            "POSTED",
        )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_compatibility_never_exposes_assisted_orders_outside_strict_scope(
        self,
    ):
        created = self._create(self._payload())
        self.assertEqual(created.status_code, 201, created.data)
        assisted_id = created.data["order_id"]
        standard = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            submit_status="SUBMITTED",
        )
        unbound = get_user_model().objects.create_user(
            username="assisted-flow-unbound-reader",
            password="x",
        )
        view = OutboundOrderViewSet.as_view({"get": "list"})
        response = view(
            self._request(
                "get",
                "/api/outbound/orders/",
                user=unbound,
            )
        )
        rows = response.data.get("results", response.data)
        ids = {row["id"] for row in rows}
        self.assertNotIn(standard.id, ids)
        self.assertNotIn(assisted_id, ids)

        operator_response = view(
            self._request(
                "get",
                "/api/outbound/orders/",
                user=self.operator,
            )
        )
        operator_rows = operator_response.data.get("results", operator_response.data)
        operator_ids = {row["id"] for row in operator_rows}
        self.assertEqual(operator_ids, {assisted_id})
