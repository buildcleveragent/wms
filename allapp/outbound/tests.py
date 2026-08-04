import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound import services as ob_services
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import OutboundOrderViewSet, PickTaskViewSet
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask, WmsTaskLine


class OutboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Outbound", code="OWN-OUT")
        self.user = get_user_model().objects.create_user(
            username="outbound-sales", password="x"
        )
        self.warehouse = Warehouse.objects.create(
            code="WH-OUT-1", name="Warehouse Outbound 1"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWOUT1",
            name="Subwarehouse Outbound 1",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWOUT1-01-01-01",
            name="Outbound Location 1",
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUS-OUT",
            name="Customer Outbound",
        )
        self.base_uom = ProductUom.objects.create(
            code="PCS-OUT", name="件", is_active=True
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-OUT",
            name="Outbound Product",
            sku="SKU-OUT",
            base_uom=self.base_uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
            batch_control=False,
            expiry_control=False,
        )
        self.api_user = get_user_model().objects.create_user(
            username="outbound-api",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.api_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="submit_outbound_as_owner_buyers",
            )
        )
        UserRoleScope.objects.create(
            user=self.api_user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.owner_manager = get_user_model().objects.create_user(
            username="outbound-owner-manager",
            password="x",
            owner=self.owner,
        )
        self.owner_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="approve_outbound_as_owner_manager",
            )
        )
        UserRoleScope.objects.create(
            user=self.owner_manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        self.operator = get_user_model().objects.create_user(
            username="outbound-operator",
            password="x",
            warehouse=self.warehouse,
        )
        self.operator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking",
                codename="claim_task_as_wh_operator",
            )
        )
        UserRoleScope.objects.create(
            user=self.operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.factory = APIRequestFactory()

    def _api_request(self, method, path, data=None, *, user=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=user or self.api_user)
        return request

    def test_outbound_order_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            OutboundOrder.objects.create(
                owner=self.owner,
                customer=self.customer,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_repeated_owner_approval_is_rejected_without_double_allocation(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.user,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("3.000"),
            base_price=Decimal("10.0000"),
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

        order.owner_approve(by_user=self.user, allow_backorder=False)
        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        with self.assertRaises(ValidationError):
            order.owner_approve(by_user=self.user, allow_backorder=False)

        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(WmsTask.objects.filter(pk=task.pk).count(), 1)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(task.lines.first().qty_plan, Decimal("3.000"))

    def test_outbound_order_api_submit_reject_and_cancel_status_transitions(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.api_user,
            submit_status="DRAFT",
            approval_status="OWNER_PENDING",
        )

        submit_view = OutboundOrderViewSet.as_view({"post": "submit"})
        response = submit_view(
            self._api_request("post", f"/api/outbound/orders/{order.id}/submit/"),
            pk=order.id,
        )
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(order.submit_status, "SUBMITTED")

        reject_view = OutboundOrderViewSet.as_view({"post": "owner_reject"})
        response = reject_view(
            self._api_request(
                "post",
                f"/api/outbound/orders/{order.id}/owner-reject/",
                data={"reason": "请修改订单"},
                user=self.owner_manager,
            ),
            pk=order.id,
        )
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(order.approval_status, "OWNER_REJECTED")
        self.assertEqual(order.approved_by_ownermanager_id, self.owner_manager.id)

        cancel_view = OutboundOrderViewSet.as_view({"post": "cancel"})
        response = cancel_view(
            self._api_request(
                "post",
                f"/api/outbound/orders/{order.id}/cancel/",
                user=self.owner_manager,
            ),
            pk=order.id,
        )
        order.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(order.approval_status, "CANCELLED")
        self.assertEqual(order.close_reason, "货主管理员取消订单")

    def test_import_drop_ship_template_download_is_available_to_authenticated_user(
        self,
    ):
        view = OutboundOrderViewSet.as_view({"get": "import_drop_ship_template"})

        response = view(
            self._api_request("get", "/api/outbound/orders/import-drop-ship-template/")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response["Content-Type"],
        )
        self.assertIn("filename*=UTF-8", response["Content-Disposition"])

    def test_pick_task_lines_and_create_review_requires_completed_lines(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="PICK-API-1",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            review_status=WmsTask.ReviewStatus.NONE,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.location,
            qty_plan=Decimal("2.000"),
            qty_done=Decimal("1.000"),
        )

        lines_view = PickTaskViewSet.as_view({"get": "lines"})
        response = lines_view(
            self._api_request(
                "get",
                f"/api/pda/pick-tasks/{task.id}/lines/",
                user=self.operator,
            ),
            pk=task.id,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["id"], line.id)

        review_view = PickTaskViewSet.as_view({"post": "create_review_task"})
        response = review_view(
            self._api_request(
                "post",
                f"/api/pda/pick-tasks/{task.id}/create-review-task/",
                user=self.operator,
            ),
            pk=task.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("未拣完", str(response.data))

    def test_pick_task_post_blocks_picker_from_reviewing_own_task(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="PICK-API-SELF-REVIEW",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
            posting_status=WmsTask.PostingStatus.NOT_READY,
            picked_by=self.operator,
        )

        view = PickTaskViewSet.as_view({"post": "post"})
        response = view(
            self._api_request(
                "post",
                f"/api/pda/pick-tasks/{task.id}/post/",
                user=self.operator,
            ),
            pk=task.id,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("拣货人不能", str(response.data))


class OutboundConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(
            name="Owner Outbound Concurrent", code="OWN-OUT-C"
        )
        self.user = get_user_model().objects.create_user(
            username="outbound-concurrent-user", password="x"
        )
        self.warehouse = Warehouse.objects.create(
            code="WH-OUT-C", name="Warehouse Outbound Concurrent"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWOUTC",
            name="SW Outbound C",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWOUTC-01-01-01",
            name="Outbound Concurrent Location 1",
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUS-OUT-C",
            name="Customer Outbound Concurrent",
        )
        self.base_uom = ProductUom.objects.create(
            code="PCS-OUT-C", name="件", is_active=True
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-OUT-C",
            name="Outbound Concurrent Product",
            sku="SKU-OUT-C",
            base_uom=self.base_uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
            batch_control=False,
            expiry_control=False,
        )

    def test_owner_approve_allocates_once_under_concurrency(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.user,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("3.000"),
            base_price=Decimal("10.0000"),
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

        reserved_task_entered = threading.Event()
        release_reserved_task = threading.Event()
        errors = []
        real_get_or_create_reserved_task = ob_services._get_or_create_reserved_task

        def fake_get_or_create_reserved_task(current_order, by_user=None):
            if not reserved_task_entered.is_set():
                reserved_task_entered.set()
                if not release_reserved_task.wait(timeout=5):
                    raise AssertionError(
                        "timed out waiting to release outbound concurrent test"
                    )
            return real_get_or_create_reserved_task(current_order, by_user=by_user)

        def invoke():
            close_old_connections()
            try:
                thread_order = OutboundOrder.objects.get(pk=order.pk)
                thread_order.owner_approve(by_user=self.user, allow_backorder=False)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch(
            "allapp.outbound.services._get_or_create_reserved_task",
            side_effect=fake_get_or_create_reserved_task,
        ):
            thread1 = threading.Thread(target=invoke)
            thread1.start()
            self.assertTrue(reserved_task_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke)
            thread2.start()

            release_reserved_task.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent outbound approval threads did not finish")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValidationError)

        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(WmsTask.objects.filter(pk=task.pk).count(), 1)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(task.lines.first().qty_plan, Decimal("3.000"))
