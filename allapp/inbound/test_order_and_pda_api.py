from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Owner, Supplier
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inbound.services import close_inbound_order_after_putaway
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import (
    ReceiveLineExtra,
    TaskAssignment,
    TaskScanLog,
    WmsTask,
    WmsTaskLine,
)


class InboundOrderAndPdaApiTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.owner = Owner.objects.create(name="API Owner", code="APIOWN1")
        self.other_owner = Owner.objects.create(name="API Other Owner", code="APIOWN2")
        self.warehouse = Warehouse.objects.create(code="APIWH1", name="API Warehouse 1")
        self.other_warehouse = Warehouse.objects.create(code="APIWH2", name="API Warehouse 2")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="APISW1",
            name="API Subwarehouse 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="APISW2",
            name="API Subwarehouse 2",
        )
        self.receive_location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="APISW1-01-01-01",
            name="Receive staging",
        )
        self.putaway_location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="APISW1-01-01-02",
            name="Storage",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            subwarehouse=self.other_subwarehouse,
            code="APISW2-01-01-01",
            name="Other storage",
        )
        self.supplier = Supplier.objects.create(
            owner=self.owner,
            code="APISUP1",
            name="API Supplier 1",
        )
        self.other_supplier = Supplier.objects.create(
            owner=self.other_owner,
            code="APISUP2",
            name="API Supplier 2",
        )
        self.uom = ProductUom.objects.create(code="APIEA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="APISKU1",
            sku="APISKU1",
            name="API Product 1",
            base_uom=self.uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )
        self.other_product = Product.objects.create(
            owner=self.other_owner,
            code="APISKU2",
            sku="APISKU2",
            name="API Product 2",
            base_uom=self.uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )

    @staticmethod
    def permission(app_label, codename):
        return Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )

    def owner_user(self, username, role, *, owner=None, warehouse=None):
        user = self.user_model.objects.create_user(
            username=username,
            password="x",
            owner=owner or self.owner,
            warehouse=warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=role,
            owner=owner or self.owner,
        )
        return user

    def warehouse_user(self, username, *, warehouse=None):
        user = self.user_model.objects.create_user(
            username=username,
            password="x",
            warehouse=warehouse or self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=warehouse or self.warehouse,
        )
        user.user_permissions.add(
            self.permission("tasking", "view_wmstask"),
            self.permission("tasking", "claim_task_as_wh_operator"),
        )
        return user

    @staticmethod
    def client_for(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def create_order(self, *, created_by, order_no, owner=None, warehouse=None):
        selected_owner = owner or self.owner
        return InboundOrder.objects.create(
            order_no=order_no,
            owner=selected_owner,
            warehouse=warehouse or self.warehouse,
            supplier=self.supplier if selected_owner == self.owner else self.other_supplier,
            created_by=created_by,
        )

    def test_owner_salesperson_create_list_detail_and_resubmit_are_actor_scoped(self):
        salesperson = self.owner_user(
            "api-salesperson",
            UserRoleScope.Role.OWNER_SALESPERSON,
        )
        colleague = self.owner_user(
            "api-colleague",
            UserRoleScope.Role.OWNER_SALESPERSON,
            warehouse=self.warehouse,
        )
        manager = self.owner_user(
            "api-manager",
            UserRoleScope.Role.OWNER_MANAGER,
        )
        salesperson.user_permissions.add(
            self.permission("inbound", "view_inboundorder"),
            self.permission("inbound", "add_inboundorder"),
            self.permission("inbound", "submit_as_owner_buyers"),
        )
        colleague.user_permissions.add(self.permission("inbound", "submit_as_owner_buyers"))
        manager.user_permissions.add(
            self.permission("inbound", "view_inboundorder"),
            self.permission("inbound", "approve_as_owner_manager"),
        )
        colleague_order = self.create_order(
            created_by=colleague,
            order_no="API-INB-COLLEAGUE",
        )
        foreign_order = self.create_order(
            created_by=colleague,
            order_no="API-INB-FOREIGN",
            owner=self.other_owner,
            warehouse=self.other_warehouse,
        )

        client = self.client_for(salesperson)
        self.assertIsNone(salesperson.warehouse_id)
        created = client.post(
            "/api/inbound/orders/",
            {
                "owner_id": self.other_owner.pk,
                "warehouse_id": self.warehouse.pk,
                "supplier_id": self.supplier.pk,
                "src_bill_no": "PO-API-001",
                "lines": [
                    {
                        "product_id": self.product.pk,
                        "base_qty": "5.000",
                        "base_price": "3.2500",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 400, created.data)

        created = client.post(
            "/api/inbound/orders/",
            {
                "warehouse_id": self.warehouse.pk,
                "supplier_id": self.supplier.pk,
                "src_bill_no": "PO-API-001",
                "lines": [{"product_id": self.product.pk, "base_qty": "5.000"}],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        order = InboundOrder.objects.get(pk=created.data["id"])
        self.assertEqual(order.owner_id, self.owner.pk)
        self.assertEqual(order.created_by_id, salesperson.pk)
        self.assertEqual(order.lines.get().base_qty, Decimal("5.000"))

        listed = client.get("/api/inbound/orders/")
        self.assertEqual(listed.status_code, 200)
        listed_ids = {row["id"] for row in listed.data["results"]}
        self.assertEqual(listed_ids, {order.pk})
        self.assertEqual(
            client.get(f"/api/inbound/orders/{colleague_order.pk}/").status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/api/inbound/orders/{foreign_order.pk}/").status_code,
            404,
        )

        submitted = client.post(f"/api/inbound/orders/{order.pk}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data["approval_status"], "OWNER_PENDING")

        # A direct permission grant cannot turn a salesperson into an owner
        # manager; role scope and capability must both be present.
        salesperson.user_permissions.add(
            self.permission("inbound", "approve_as_owner_manager")
        )
        self.assertEqual(
            client.post(f"/api/inbound/orders/{order.pk}/owner-approve/").status_code,
            403,
        )
        # The conflicting direct marker makes the security resolver fail
        # closed for that request. Remove the deliberately bad grant before
        # continuing the normal salesperson workflow.
        salesperson.user_permissions.remove(
            self.permission("inbound", "approve_as_owner_manager")
        )

        manager_client = self.client_for(manager)
        manager_list = manager_client.get("/api/inbound/orders/")
        self.assertEqual(
            {row["id"] for row in manager_list.data["results"]},
            {order.pk, colleague_order.pk},
        )
        rejected = manager_client.post(f"/api/inbound/orders/{order.pk}/owner-reject/")
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.assertEqual(rejected.data["approval_status"], "OWNER_REJECTED")

        resubmitted = client.post(f"/api/inbound/orders/{order.pk}/submit/")
        self.assertEqual(resubmitted.status_code, 200, resubmitted.data)
        self.assertEqual(resubmitted.data["approval_status"], "OWNER_PENDING")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="inbound.order.submit",
                object_id=str(order.pk),
            ).exists()
        )

    def test_receive_pda_tasks_are_pool_or_actor_scoped_and_receipt_is_idempotent(self):
        operator = self.warehouse_user("api-operator")
        other_operator = self.warehouse_user("api-other-operator")
        foreign_operator = self.warehouse_user(
            "api-foreign-operator",
            warehouse=self.other_warehouse,
        )
        task = WmsTask.objects.create(
            task_no="API-RECEIVE-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            qty_plan=Decimal("4.000"),
            qty_done=Decimal("0"),
            status=WmsTaskLine.Status.RELEASED,
        )
        ReceiveLineExtra.objects.create(line=line)
        assigned_elsewhere = WmsTask.objects.create(
            task_no="API-RECEIVE-OTHER",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
        )
        TaskAssignment.objects.create(
            task=assigned_elsewhere,
            assignee=other_operator,
        )
        foreign_task = WmsTask.objects.create(
            task_no="API-RECEIVE-FOREIGN",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.RELEASED,
            owner=self.other_owner,
            warehouse=self.other_warehouse,
        )
        TaskAssignment.objects.create(task=foreign_task, assignee=foreign_operator)

        client = self.client_for(operator)
        listed = client.get("/api/inbound/pda/tasks/?task_type=RECEIVE")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual({row["id"] for row in listed.data["results"]}, {task.pk})
        self.assertEqual(
            client.get(f"/api/inbound/pda/tasks/{assigned_elsewhere.pk}/").status_code,
            404,
        )

        payload = {
            "request_id": "api-receive-0001",
            "line_id": line.pk,
            "location_id": self.receive_location.pk,
            "qty_ok": "4.000",
            "qty_damage": "0.000",
            "qty_reject": "0.000",
            "finalize": True,
        }
        denied = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-receipt/",
            payload,
            format="json",
        )
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertFalse(TaskScanLog.objects.filter(task=task).exists())

        claimed = client.post(f"/api/inbound/pda/tasks/{task.pk}/claim/")
        self.assertEqual(claimed.status_code, 200, claimed.data)
        started = client.post(f"/api/inbound/pda/tasks/{task.pk}/start/")
        self.assertEqual(started.status_code, 200, started.data)
        received = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-receipt/",
            payload,
            format="json",
        )
        self.assertEqual(received.status_code, 200, received.data)
        self.assertFalse(received.data["idempotent"])
        self.assertEqual(received.data["task"]["status"], WmsTask.Status.COMPLETED)
        replay = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-receipt/",
            payload,
            format="json",
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        conflict = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-receipt/",
            {**payload, "qty_ok": "3.000"},
            format="json",
        )
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertTrue(
            TaskScanLog.objects.filter(
                task=task,
                task_line=line,
                status=TaskScanLog.ScanStatus.OK,
                qty_base_delta=Decimal("4.000"),
            ).exists()
        )

        over_task = WmsTask.objects.create(
            task_no="API-RECEIVE-OVER",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        over_line = WmsTaskLine.objects.create(
            task=over_task,
            product=self.product,
            qty_plan=Decimal("2.000"),
            qty_done=Decimal("0"),
            status=WmsTaskLine.Status.RELEASED,
        )
        ReceiveLineExtra.objects.create(line=over_line)
        self.assertEqual(
            client.post(f"/api/inbound/pda/tasks/{over_task.pk}/claim/").status_code,
            200,
        )
        self.assertEqual(
            client.post(f"/api/inbound/pda/tasks/{over_task.pk}/start/").status_code,
            200,
        )
        over_payload = {
            "request_id": "api-receive-over1",
            "line_id": over_line.pk,
            "location_id": self.receive_location.pk,
            "qty_ok": "3.000",
            "qty_damage": "0.000",
            "qty_reject": "0.000",
            "finalize": True,
        }
        missing_reason = client.post(
            f"/api/inbound/pda/tasks/{over_task.pk}/record-receipt/",
            over_payload,
            format="json",
        )
        self.assertEqual(missing_reason.status_code, 400, missing_reason.data)
        accepted_over = client.post(
            f"/api/inbound/pda/tasks/{over_task.pk}/record-receipt/",
            {**over_payload, "variance_reason": "供应商多送 1 件"},
            format="json",
        )
        self.assertEqual(accepted_over.status_code, 200, accepted_over.data)
        over_line.refresh_from_db()
        self.assertEqual(over_line.remark, "供应商多送 1 件")

    def test_putaway_pda_records_target_and_rejects_cross_warehouse_location(self):
        operator = self.warehouse_user("api-putaway-operator")
        order = self.create_order(created_by=operator, order_no="API-INB-PUTAWAY-SOURCE")
        InboundOrderLine.objects.create(
            order=order,
            product=self.product,
            base_qty=Decimal("2.000"),
        )
        receive_task = WmsTask.objects.create(
            task_no="API-RECEIVE-PUTAWAY-SOURCE",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(order.pk),
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        task = WmsTask.objects.create(
            task_no="API-PUTAWAY-1",
            task_type=WmsTask.TaskType.PUTAWAY,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="tasking",
            source_model="WmsTask",
            source_pk=str(receive_task.pk),
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.receive_location,
            qty_plan=Decimal("2.000"),
            qty_done=Decimal("0"),
            status=WmsTaskLine.Status.RELEASED,
        )
        client = self.client_for(operator)
        self.assertEqual(
            client.post(f"/api/inbound/pda/tasks/{task.pk}/claim/").status_code,
            200,
        )
        self.assertEqual(
            client.post(f"/api/inbound/pda/tasks/{task.pk}/start/").status_code,
            200,
        )
        cross_warehouse = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                "request_id": "api-putaway-bad1",
                "line_id": line.pk,
                "to_location_id": self.other_location.pk,
                "qty": "2.000",
            },
            format="json",
        )
        self.assertEqual(cross_warehouse.status_code, 404, cross_warehouse.data)

        payload = {
            "request_id": "api-putaway-0001",
            "line_id": line.pk,
            "to_location_id": self.putaway_location.pk,
            "qty": "2.000",
        }
        moved = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            payload,
            format="json",
        )
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertFalse(moved.data["idempotent"])
        self.assertEqual(moved.data["task"]["status"], WmsTask.Status.COMPLETED)
        self.assertEqual(moved.data["task"]["review_status"], WmsTask.ReviewStatus.PENDING)
        self.assertEqual(moved.data["task"]["posting_status"], WmsTask.PostingStatus.NOT_READY)
        line.refresh_from_db()
        self.assertEqual(line.to_location_id, self.putaway_location.pk)
        self.assertEqual(line.qty_done, Decimal("2.000"))
        replay = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            payload,
            format="json",
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        self.assertTrue(
            AuditEvent.objects.filter(
                action="inbound.putaway.record",
                object_id=str(task.pk),
            ).exists()
        )

        WmsTask.objects.filter(pk=task.pk).update(
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        task.refresh_from_db()
        closed_order = close_inbound_order_after_putaway(task, by_user=operator)
        self.assertEqual(closed_order.pk, order.pk)
        order.refresh_from_db()
        self.assertTrue(order.is_closed)
        self.assertEqual(order.close_reason, "上架完成并已过账")
