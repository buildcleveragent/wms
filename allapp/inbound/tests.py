import datetime
import io
import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone
from openpyxl import load_workbook
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding, Supplier
from allapp.inbound.admin import InboundOrderAdmin, PdaNoOrderReceiveAdmin
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)
from allapp.inbound.models import (
    InboundOrder,
    InboundReceipt,
    Lot,
    LotWarehouse,
    NoOrderReceiveRequest,
    PdaNoOrderReceive,
)
from allapp.inbound.services import (
    create_receive_task_draft,
    receive_goods_without_order,
)
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.plugins.handlers import DefaultPostingHandler


class InboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inbound", code="OWN-INB")
        self.warehouse = Warehouse.objects.create(code="WH-INB-1", name="Warehouse Inbound 1")
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-INB", name="Supplier Inbound")
        self.user = get_user_model().objects.create_user(username="inbound-user", password="x")
        self.base_uom = ProductUom.objects.create(code="PCS-INB", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INB",
            name="Inbound Product",
            sku="SKU-INB",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )

    def test_inbound_order_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            InboundOrder.objects.create(
                owner=self.owner,
                supplier=self.supplier,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_inbound_receipt_derives_warehouse_from_order(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
        )

        receipt = InboundReceipt.objects.create(
            receipt_no="RCPT-INB-1",
            order=order,
            owner=self.owner,
            supplier=self.supplier,
            biz_date=datetime.date(2026, 3, 29),
        )

        self.assertEqual(receipt.warehouse_id, self.warehouse.id)

    def test_lot_warehouse_requires_explicit_warehouse(self):
        lot = Lot.objects.create(owner=self.owner, product_code="SKU-INB", lot_no="LOT-INB-1")

        with self.assertRaises(ValidationError) as exc:
            LotWarehouse.objects.create(
                lot=lot,
                owner=self.owner,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_create_receive_task_draft_is_idempotent(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("5.000"),
            base_price=Decimal("8.0000"),
        )

        first_task = create_receive_task_draft(order, by_user=self.user)
        second_task = create_receive_task_draft(order, by_user=self.user)

        self.assertEqual(first_task.id, second_task.id)
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(order.pk),
            ).count(),
            1,
        )
        self.assertEqual(first_task.lines.count(), 1)

    def test_pda_no_order_receive_admin_shows_only_pda_no_order_receipts(self):
        admin_user = get_user_model().objects.create_superuser(
            username="pda-receive-admin",
            email="pda-receive-admin@example.com",
            password="x",
        )

        def make_task(task_no, **overrides):
            data = {
                "owner": self.owner,
                "warehouse": self.warehouse,
                "task_no": task_no,
                "task_type": WmsTask.TaskType.RECEIVE,
                "status": WmsTask.Status.COMPLETED,
                "review_status": WmsTask.ReviewStatus.APPROVED,
                "posting_status": WmsTask.PostingStatus.POSTED,
            }
            data.update(overrides)
            return WmsTask.objects.create(**data)

        current_pda = make_task(
            "RK-PDA-CURRENT",
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        legacy_pda = make_task("RK-PDA-LEGACY", posting_note=PDA_NO_ORDER_RECEIVE_NOTE)
        formal_receive = make_task(
            "SH-FORMAL",
            source_app="inbound",
            source_model="InboundOrder",
            posting_note="入库订单收货",
        )
        make_task(
            "PUT-PDA-NOTE",
            task_type=WmsTask.TaskType.PUTAWAY,
            posting_note=PDA_NO_ORDER_RECEIVE_NOTE,
        )

        request = RequestFactory().get("/admin/inbound/pdanoorderreceive/")
        request.user = admin_user
        model_admin = PdaNoOrderReceiveAdmin(PdaNoOrderReceive, admin.site)

        ids = set(model_admin.get_queryset(request).values_list("id", flat=True))

        self.assertIn(current_pda.id, ids)
        self.assertIn(legacy_pda.id, ids)
        self.assertNotIn(formal_receive.id, ids)


class InboundAuthorizationAndWorkflowTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Inbound Scope Owner", code="INBSCO1")
        self.other_owner = Owner.objects.create(
            name="Inbound Other Owner", code="INBSCO2"
        )
        self.warehouse = Warehouse.objects.create(
            code="INBWH1", name="Inbound Warehouse 1"
        )
        self.other_warehouse = Warehouse.objects.create(
            code="INBWH2", name="Inbound Warehouse 2"
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="INBSW1",
            name="Inbound Subwarehouse 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="INBSW2",
            name="Inbound Subwarehouse 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="INBSW1-01-01-01",
            name="Inbound Receive Location",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="INBSW2-01-01-01",
            name="Inbound Other Location",
        )
        self.supplier = Supplier.objects.create(
            owner=self.owner,
            code="INBSS1",
            name="Inbound Scope Supplier",
        )
        self.other_supplier = Supplier.objects.create(
            owner=self.other_owner,
            code="INBSS2",
            name="Inbound Other Supplier",
        )
        self.base_uom = ProductUom.objects.create(
            code="INBPCS",
            name="件",
            is_active=True,
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="INBSKU1",
            name="Inbound Scope Product",
            sku="INBSKU1",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )
        self.user_model = get_user_model()

    @staticmethod
    def _permission(app_label, codename):
        return Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )

    def _order(self, order_no, *, other=False, approval_status="OWNER_PENDING"):
        return InboundOrder.objects.create(
            order_no=order_no,
            owner=self.other_owner if other else self.owner,
            supplier=self.other_supplier if other else self.supplier,
            warehouse=self.other_warehouse if other else self.warehouse,
            submit_status="SUBMITTED",
            approval_status=approval_status,
        )

    def test_rejected_order_can_be_resubmitted_through_both_review_stages(self):
        user = self.user_model.objects.create_user(
            username="inbound-workflow", password="x"
        )
        order = self._order("INB-WORKFLOW-1")

        order.owner_reject(user)
        self.assertEqual(order.submit_status, "DRAFT")
        self.assertEqual(order.approval_status, "OWNER_REJECTED")

        order.submit_by_owner_buyers(user)
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "OWNER_PENDING")

        order.owner_approve(user)
        self.assertEqual(order.approval_status, "WHS_PENDING")

        order.wh_reject(user)
        self.assertEqual(order.submit_status, "DRAFT")
        self.assertEqual(order.approval_status, "WHS_REJECTED")

        order.submit_by_owner_buyers(user)
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "OWNER_PENDING")

    def test_order_admin_and_action_are_both_owner_scoped(self):
        user = self.user_model.objects.create_user(
            username="inbound-owner-manager",
            password="x",
            owner=self.owner,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        user.user_permissions.add(
            self._permission("inbound", "approve_as_owner_manager")
        )
        own_order = self._order("INB-ADMIN-OWN")
        foreign_order = self._order("INB-ADMIN-FOREIGN", other=True)
        request = RequestFactory().post("/admin/inbound/inboundorder/")
        request.user = user
        model_admin = InboundOrderAdmin(InboundOrder, admin.site)
        model_admin.message_user = mock.Mock()

        visible_ids = set(
            model_admin.get_queryset(request).values_list("pk", flat=True)
        )
        self.assertEqual(visible_ids, {own_order.pk})

        model_admin.action_owner_approve(
            request,
            InboundOrder.objects.filter(pk__in=[own_order.pk, foreign_order.pk]),
        )
        own_order.refresh_from_db()
        foreign_order.refresh_from_db()
        self.assertEqual(own_order.approval_status, "WHS_PENDING")
        self.assertEqual(foreign_order.approval_status, "OWNER_PENDING")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="inbound.order.owner_approve",
                object_id=str(own_order.pk),
            ).exists()
        )

    def test_owner_salesperson_sees_and_submits_only_own_orders(self):
        salesperson = self.user_model.objects.create_user(
            username="inbound-owner-salesperson",
            password="x",
            owner=self.owner,
        )
        colleague = self.user_model.objects.create_user(
            username="inbound-owner-colleague",
            password="x",
            owner=self.owner,
        )
        UserRoleScope.objects.create(
            user=salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        salesperson.user_permissions.add(
            self._permission("inbound", "submit_as_owner_buyers")
        )
        own_order = InboundOrder.objects.create(
            order_no="INB-SALES-OWN",
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            created_by=salesperson,
        )
        colleague_order = InboundOrder.objects.create(
            order_no="INB-SALES-COLLEAGUE",
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            created_by=colleague,
        )
        request = RequestFactory().post("/admin/inbound/inboundorder/")
        request.user = salesperson
        model_admin = InboundOrderAdmin(InboundOrder, admin.site)
        model_admin.message_user = mock.Mock()

        visible_ids = set(
            model_admin.get_queryset(request).values_list("pk", flat=True)
        )
        self.assertEqual(visible_ids, {own_order.pk})

        model_admin.action_owner_buyers_submit(
            request,
            InboundOrder.objects.filter(
                pk__in=[own_order.pk, colleague_order.pk]
            ),
        )
        own_order.refresh_from_db()
        colleague_order.refresh_from_db()
        self.assertEqual(own_order.submit_status, "SUBMITTED")
        self.assertEqual(own_order.approval_status, "OWNER_PENDING")
        self.assertEqual(colleague_order.submit_status, "DRAFT")
        self.assertEqual(colleague_order.approval_status, "NOT_READY")

    def test_receive_without_order_requires_dedicated_permission(self):
        user = self.user_model.objects.create_user(
            username="inbound-no-order-denied",
            password="x",
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(user)

        response = client.post(
            "/api/inbound/receive_without_order/",
            {
                "request_id": "receive-denied-0001",
                "owner_id": self.owner.pk,
                "warehouse_id": self.warehouse.pk,
                "location_id": self.location.pk,
                "items": [{"product_id": self.product.pk, "qty": "2.0000"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            WmsTask.objects.filter(task_type=WmsTask.TaskType.RECEIVE).exists()
        )

    def test_receive_without_order_replays_same_request_and_rejects_changed_payload(
        self,
    ):
        user = self.user_model.objects.create_user(
            username="inbound-no-order-operator",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(self._permission("accounts", "receive_without_order"))
        client = APIClient()
        client.force_authenticate(user)
        payload = {
            "request_id": "receive-idempotent-0001",
            "owner_id": self.owner.pk,
            "warehouse_id": self.warehouse.pk,
            "location_id": self.location.pk,
            "items": [{"product_id": self.product.pk, "qty": "2.0000"}],
        }

        with mock.patch("allapp.inbound.services.save_receiving_snapshot"), mock.patch(
            "allapp.inbound.services._run_posting_handler",
            return_value={"affected_tx_count": 1},
        ):
            created = client.post(
                "/api/inbound/receive_without_order/",
                payload,
                format="json",
            )
            replayed = client.post(
                "/api/inbound/receive_without_order/",
                payload,
                format="json",
            )
            changed_payload = {
                **payload,
                "items": [{"product_id": self.product.pk, "qty": "3.0000"}],
            }
            conflict = client.post(
                "/api/inbound/receive_without_order/",
                changed_payload,
                format="json",
            )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(replayed.status_code, 200, replayed.data)
        self.assertEqual(replayed.data["task_id"], created.data["task_id"])
        self.assertTrue(replayed.data["idempotent"])
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(NoOrderReceiveRequest.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="inbound.receive_without_order.post"
            ).count(),
            1,
        )
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            ).count(),
            1,
        )

    def test_receive_without_order_rejects_foreign_owner_product_without_writes(self):
        foreign_product = Product.objects.create(
            owner=self.other_owner,
            code="INBSKU-FOREIGN",
            name="Inbound Foreign Product",
            sku="INBSKU-FOREIGN",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
        )
        user = self.user_model.objects.create_user(
            username="inbound-no-order-foreign-product",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(self._permission("accounts", "receive_without_order"))
        client = APIClient()
        client.force_authenticate(user)

        response = client.post(
            "/api/inbound/receive_without_order/",
            {
                "request_id": "receive-foreign-product-0001",
                "owner_id": self.owner.pk,
                "warehouse_id": self.warehouse.pk,
                "location_id": self.location.pk,
                "items": [{"product_id": foreign_product.pk, "qty": "2.0000"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(NoOrderReceiveRequest.objects.exists())
        self.assertFalse(
            WmsTask.objects.filter(
                source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            ).exists()
        )

    def test_receive_without_order_rejects_unbound_owner_without_writes(self):
        unbound_product = Product.objects.create(
            owner=self.other_owner,
            code="INBSKU-UNBOUND",
            name="Inbound Unbound Product",
            sku="INBSKU-UNBOUND",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
        )
        user = self.user_model.objects.create_user(
            username="inbound-no-order-unbound-owner",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(self._permission("accounts", "receive_without_order"))
        client = APIClient()
        client.force_authenticate(user)

        response = client.post(
            "/api/inbound/receive_without_order/",
            {
                "request_id": "receive-unbound-owner-0001",
                "owner_id": self.other_owner.pk,
                "warehouse_id": self.warehouse.pk,
                "location_id": self.location.pk,
                "items": [{"product_id": unbound_product.pk, "qty": "2.0000"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("未授权当前仓库", str(response.data))
        self.assertFalse(NoOrderReceiveRequest.objects.exists())
        self.assertFalse(
            WmsTask.objects.filter(
                source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            ).exists()
        )

    def test_receive_without_order_posting_failure_rolls_back_and_can_retry(self):
        user = self.user_model.objects.create_user(
            username="inbound-no-order-retry",
            password="x",
        )
        kwargs = {
            "owner_id": self.owner.pk,
            "warehouse_id": self.warehouse.pk,
            "location_id": self.location.pk,
            "items": [{"product_id": self.product.pk, "qty": "2.0000"}],
            "request_id": "receive-posting-retry-0001",
            "by_user": user,
        }

        with mock.patch("allapp.inbound.services.save_receiving_snapshot"), mock.patch(
            "allapp.inbound.services._run_posting_handler",
            side_effect=[RuntimeError("posting unavailable"), {"affected_tx_count": 1}],
        ):
            with self.assertRaisesMessage(RuntimeError, "posting unavailable"):
                receive_goods_without_order(**kwargs)

            self.assertFalse(NoOrderReceiveRequest.objects.exists())
            self.assertFalse(
                WmsTask.objects.filter(
                    source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                    source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
                ).exists()
            )

            retried = receive_goods_without_order(**kwargs)

        self.assertFalse(retried["idempotent"])
        self.assertEqual(NoOrderReceiveRequest.objects.count(), 1)
        self.assertEqual(
            WmsTask.objects.filter(
                source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
                source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            ).count(),
            1,
        )

    def test_receive_export_and_print_are_scoped_and_use_actual_quantity(self):
        user = self.user_model.objects.create_user(
            username="inbound-task-viewer",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(self._permission("tasking", "view_wmstask"))
        own_task = WmsTask.objects.create(
            task_no="INB-EXPORT-OWN",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.owner,
            warehouse=self.warehouse,
            status=WmsTask.Status.RELEASED,
        )
        line = WmsTaskLine.objects.create(
            task=own_task,
            product=self.product,
            to_location=self.location,
            qty_plan=Decimal("5.000"),
            qty_done=Decimal("3.000"),
            status=WmsTaskLine.Status.RELEASED,
        )
        TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=own_task,
            task_line=line,
            product=self.product,
            location=self.location,
            qty_base_delta=Decimal("3.000000"),
            lot_no="LOT-ACTUAL",
            status=TaskScanLog.ScanStatus.OK,
            fp="inbound-export-actual-fp",
            scan_snapshot_rev=1,
        )
        other_task = WmsTask.objects.create(
            task_no="INB-EXPORT-OTHER",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.other_owner,
            warehouse=self.other_warehouse,
            status=WmsTask.Status.RELEASED,
        )

        api_client = APIClient()
        api_client.force_authenticate(user)
        exported = api_client.get(
            f"/api/inbound/receive_task/{own_task.pk}/export_excel/"
        )
        denied_export = api_client.get(
            f"/api/inbound/receive_task/{other_task.pk}/export_excel/"
        )
        anonymous_export = APIClient().get(
            f"/api/inbound/receive_task/{own_task.pk}/export_excel/"
        )

        self.assertEqual(exported.status_code, 200)
        workbook = load_workbook(io.BytesIO(exported.content), data_only=True)
        worksheet = workbook.active
        self.assertEqual(worksheet["J7"].value, 5)
        self.assertEqual(worksheet["K7"].value, 3)
        self.assertEqual(worksheet["F7"].value, "LOT-ACTUAL")
        self.assertEqual(denied_export.status_code, 404)
        self.assertIn(anonymous_export.status_code, (401, 403))

        web_client = Client()
        web_client.force_login(user)
        printed = web_client.get(f"/api/inbound/receive_task/{own_task.pk}/print/")
        denied_print = web_client.get(
            f"/api/inbound/receive_task/{other_task.pk}/print/"
        )
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, "实际收货数量")
        self.assertContains(printed, "3.000")
        self.assertEqual(denied_print.status_code, 404)

    def test_receive_transactions_create_one_traceable_ready_putaway_task(self):
        user = self.user_model.objects.create_user(
            username="inbound-putaway", password="x"
        )
        receive_task = WmsTask.objects.create(
            task_no="INB-RECEIVE-FOR-PUTAWAY",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.owner,
            warehouse=self.warehouse,
            status=WmsTask.Status.RELEASED,
        )
        tx = InventoryTransaction.objects.create(
            tx_type="RECEIVE",
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("4.0000"),
            src_model="WmsTask",
            src_id=receive_task.pk,
            src_line_id=101,
            src_no=receive_task.task_no,
        )
        handler = DefaultPostingHandler()

        with mock.patch(
            "allapp.tasking.plugins.handlers.DocSequence.next_code",
            return_value="INB-PUTAWAY-AUTO",
        ):
            first = handler._create_putaway_task(receive_task, user, timezone.now())
            second = handler._create_putaway_task(receive_task, user, timezone.now())

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, WmsTask.Status.READY)
        self.assertEqual(first.source_app, "tasking")
        self.assertEqual(first.source_model, "WmsTask")
        self.assertEqual(first.source_pk, str(receive_task.pk))
        putaway_line = first.lines.get()
        self.assertEqual(putaway_line.from_location_id, self.location.pk)
        self.assertEqual(putaway_line.qty_plan, Decimal("4.000"))
        self.assertEqual(putaway_line.src_model, "inventory.InventoryTransaction")
        self.assertEqual(putaway_line.src_id, tx.pk)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="inbound.putaway.create",
                object_id=str(first.pk),
            ).exists()
        )
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PUTAWAY,
                source_app="tasking",
                source_model="WmsTask",
                source_pk=str(receive_task.pk),
            ).count(),
            1,
        )


@skipUnless(
    connection.vendor == "mysql",
    "row-lock concurrency semantics are verified on the production MySQL backend",
)
class InboundConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inbound Concurrent", code="OWN-INB-C")
        self.warehouse = Warehouse.objects.create(code="WH-INB-C", name="Warehouse Inbound Concurrent")
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-INB-C", name="Supplier Inbound Concurrent")
        self.user = get_user_model().objects.create_user(username="inbound-concurrent-user", password="x")
        self.base_uom = ProductUom.objects.create(code="PCS-INB-C", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INB-C",
            name="Inbound Concurrent Product",
            sku="SKU-INB-C",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )

    def test_create_receive_task_draft_is_single_under_concurrency(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("5.000"),
            base_price=Decimal("8.0000"),
        )

        sequence_entered = threading.Event()
        release_sequence = threading.Event()
        sequence_calls = 0
        sequence_lock = threading.Lock()
        results = [None, None]
        errors = []

        def fake_next_code(*args, **kwargs):
            nonlocal sequence_calls
            with sequence_lock:
                sequence_calls += 1
                current_call = sequence_calls
            if current_call == 1:
                sequence_entered.set()
                if not release_sequence.wait(timeout=5):
                    raise AssertionError("timed out waiting to release inbound concurrent test")
            return f"SH-CONC-{current_call}"

        def invoke(index):
            close_old_connections()
            try:
                results[index] = create_receive_task_draft(order, by_user=self.user)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch("allapp.inbound.services.DocSequence.next_code", side_effect=fake_next_code):
            thread1 = threading.Thread(target=invoke, args=(0,))
            thread1.start()
            self.assertTrue(sequence_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke, args=(1,))
            thread2.start()

            release_sequence.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent inbound task creation threads did not finish")
        if errors:
            raise errors[0]

        self.assertEqual(sequence_calls, 1)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(order.pk),
            ).count(),
            1,
        )
        self.assertEqual(results[0].lines.count(), 1)
