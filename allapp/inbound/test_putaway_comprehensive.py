"""Comprehensive pre-release regression coverage for inbound putaway work."""

import datetime
import threading
from decimal import Decimal
from unittest import mock, skipUnless

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Owner, Supplier
from allapp.inbound.models import InboundOrder
from allapp.inbound.services import close_inbound_order_after_putaway
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import (
    PutawayLineExtra,
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.plugins.handlers import DefaultPostingHandler
from allapp.tasking.services import approve_task, publish_task, reject_task

pytestmark = pytest.mark.integration


class PutawayFixtureMixin:
    """Small, explicit fixture shared by API and concurrency checks."""

    def build_fixture(self):
        self.user_model = get_user_model()
        self.owner = Owner.objects.create(code="PUT-OWN-A", name="Putaway owner A")
        self.other_owner = Owner.objects.create(code="PUT-OWN-B", name="Putaway owner B")
        self.warehouse = Warehouse.objects.create(code="PUT-WH-A", name="Putaway warehouse A")
        self.other_warehouse = Warehouse.objects.create(code="PUT-WH-B", name="Putaway warehouse B")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse, code="PUTSWA", name="Putaway SW A"
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse, code="PUTSWB", name="Putaway SW B"
        )
        self.source = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="PUTSWA-01-01-01",
            name="Putaway staging",
        )
        self.destination = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="PUTSWA-01-01-02",
            name="Putaway destination 1",
        )
        self.alternate_destination = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="PUTSWA-01-01-03",
            name="Putaway destination 2",
        )
        self.frozen_location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="PUTSWA-01-01-04",
            name="Frozen destination",
            is_frozen=True,
        )
        self.disabled_location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="PUTSWA-01-01-05",
            name="Disabled destination",
            is_disabled=True,
        )
        self.foreign_location = Location.objects.create(
            warehouse=self.other_warehouse,
            subwarehouse=self.other_subwarehouse,
            code="PUTSWB-01-01-01",
            name="Foreign destination",
        )
        self.uom = ProductUom.objects.create(code="PUT-EA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="PUT-SKU-A",
            sku="PUT-SKU-A",
            name="Putaway product A",
            base_uom=self.uom,
            batch_control=False,
            expiry_control=False,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.other_product = Product.objects.create(
            owner=self.owner,
            code="PUT-SKU-B",
            sku="PUT-SKU-B",
            name="Putaway product B",
            base_uom=self.uom,
            batch_control=False,
            expiry_control=False,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.foreign_product = Product.objects.create(
            owner=self.other_owner,
            code="PUT-SKU-FOREIGN",
            sku="PUT-SKU-FOREIGN",
            name="Putaway foreign product",
            base_uom=self.uom,
            batch_control=False,
            expiry_control=False,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )

    @staticmethod
    def permission(app_label, codename):
        return Permission.objects.get(
            content_type__app_label=app_label,
            codename=codename,
        )

    def operator(self, username, *, warehouse=None):
        selected_warehouse = warehouse or self.warehouse
        user = self.user_model.objects.create_user(
            username=username,
            password="x",
            warehouse=selected_warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=selected_warehouse,
        )
        user.user_permissions.add(
            self.permission("tasking", "view_wmstask"),
            self.permission("tasking", "claim_task_as_wh_operator"),
        )
        return user

    def manager(self, username="putaway-manager"):
        user = self.user_model.objects.create_user(
            username=username,
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(
            self.permission("tasking", "view_wmstask"),
            self.permission("tasking", "taskconfirm_as_wh_manager"),
        )
        return user

    @staticmethod
    def client_for(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def make_task(
        self,
        task_no,
        *,
        status=WmsTask.Status.RELEASED,
        warehouse=None,
        owner=None,
        product=None,
        source=None,
        qty=Decimal("5.000"),
        task_type=WmsTask.TaskType.PUTAWAY,
    ):
        selected_warehouse = warehouse or self.warehouse
        selected_owner = owner or self.owner
        task = WmsTask.objects.create(
            task_no=task_no,
            task_type=task_type,
            owner=selected_owner,
            warehouse=selected_warehouse,
            status=status,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=product or self.product,
            from_location=source or self.source,
            qty_plan=qty,
            qty_done=Decimal("0"),
            status=(
                WmsTaskLine.Status.RELEASED
                if status == WmsTask.Status.RELEASED
                else WmsTaskLine.Status.READY
            ),
        )
        return task, line

    def claim_and_start(self, client, task):
        claimed = client.post(f"/api/inbound/pda/tasks/{task.pk}/claim/")
        self.assertEqual(claimed.status_code, 200, claimed.data)
        started = client.post(f"/api/inbound/pda/tasks/{task.pk}/start/")
        self.assertEqual(started.status_code, 200, started.data)


@pytest.mark.api
class PutawayPdaApiComprehensiveTests(PutawayFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_auth_scope_search_pagination_and_assignment_visibility(self):
        operator = self.operator("putaway-api-operator")
        other_operator = self.operator("putaway-api-other")
        foreign_operator = self.operator("putaway-api-foreign", warehouse=self.other_warehouse)
        visible, _ = self.make_task("PUT-SEARCH-VISIBLE")
        assigned, _ = self.make_task("PUT-SEARCH-ASSIGNED")
        TaskAssignment.objects.create(task=assigned, assignee=other_operator)
        foreign_source = Location.objects.create(
            warehouse=self.other_warehouse,
            subwarehouse=self.other_subwarehouse,
            code="PUTSWB-01-01-02",
        )
        foreign, _ = self.make_task(
            "PUT-SEARCH-FOREIGN",
            warehouse=self.other_warehouse,
            owner=self.other_owner,
            product=self.foreign_product,
            source=foreign_source,
        )

        anonymous = APIClient().get("/api/inbound/pda/tasks/?task_type=PUTAWAY")
        self.assertIn(anonymous.status_code, (401, 403))

        client = self.client_for(operator)
        listed = client.get("/api/inbound/pda/tasks/?task_type=PUTAWAY&search=VISIBLE&page_size=1")
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([row["id"] for row in listed.data["results"]], [visible.pk])
        self.assertEqual(listed.data["count"], 1)
        self.assertEqual(
            client.get(f"/api/inbound/pda/tasks/{assigned.pk}/").status_code,
            404,
        )
        self.assertEqual(client.get(f"/api/inbound/pda/tasks/{foreign.pk}/").status_code, 404)
        self.assertEqual(
            self.client_for(foreign_operator)
            .get(f"/api/inbound/pda/tasks/{visible.pk}/")
            .status_code,
            404,
        )

    def test_location_search_excludes_frozen_disabled_and_foreign_locations(self):
        operator = self.operator("putaway-location-operator")
        task, _ = self.make_task("PUT-LOCATIONS")
        response = self.client_for(operator).get(
            f"/api/inbound/pda/tasks/{task.pk}/locations/?search=PUT"
        )
        self.assertEqual(response.status_code, 200, response.data)
        ids = {row["id"] for row in response.data}
        self.assertIn(self.destination.pk, ids)
        self.assertIn(self.alternate_destination.pk, ids)
        self.assertNotIn(self.frozen_location.pk, ids)
        self.assertNotIn(self.disabled_location.pk, ids)
        self.assertNotIn(self.foreign_location.pk, ids)

    def test_claim_start_partial_completion_and_audit_trail(self):
        operator = self.operator("putaway-partial-operator")
        task, line = self.make_task("PUT-PARTIAL", qty=Decimal("2.345"))
        client = self.client_for(operator)

        denied = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                "request_id": "putaway-unclaimed-1",
                "line_id": line.pk,
                "to_location_id": self.destination.pk,
                "qty": "1.000",
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403, denied.data)
        self.claim_and_start(client, task)

        first_payload = {
            "request_id": "putaway-partial-1",
            "line_id": line.pk,
            "to_location_id": self.destination.pk,
            "qty": "1.234",
        }
        first = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            first_payload,
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["task"]["status"], WmsTask.Status.IN_PROGRESS)
        returned_line = first.data["task"]["lines"][0]
        self.assertEqual(Decimal(returned_line["qty_done"]), Decimal("1.234"))
        self.assertEqual(Decimal(returned_line["qty_pending"]), Decimal("1.111"))

        second = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                "request_id": "putaway-partial-2",
                "line_id": line.pk,
                "to_location_id": self.destination.pk,
                "qty": "1.111",
            },
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data["task"]["status"], WmsTask.Status.COMPLETED)
        self.assertEqual(second.data["task"]["review_status"], WmsTask.ReviewStatus.PENDING)
        line.refresh_from_db()
        self.assertEqual(line.qty_done, Decimal("2.345"))
        self.assertEqual(line.to_location_id, self.destination.pk)
        self.assertEqual(
            TaskScanLog.objects.filter(task=task, status=TaskScanLog.ScanStatus.OK).count(),
            2,
        )
        self.assertTrue(
            TaskStatusLog.objects.filter(
                task=task,
                old_status=WmsTask.Status.RELEASED,
                new_status=WmsTask.Status.IN_PROGRESS,
            ).exists()
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action="inbound.putaway.record", object_id=str(task.pk)
            ).count(),
            2,
        )

    def test_idempotent_replay_conflict_and_target_location_lock(self):
        operator = self.operator("putaway-idempotent-operator")
        task, line = self.make_task("PUT-IDEMPOTENCY")
        client = self.client_for(operator)
        self.claim_and_start(client, task)
        payload = {
            "request_id": "putaway-idempotency-1",
            "line_id": line.pk,
            "to_location_id": self.destination.pk,
            "qty": "1.000",
        }
        first = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/", payload, format="json"
        )
        replay = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/", payload, format="json"
        )
        conflict = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {**payload, "qty": "2.000"},
            format="json",
        )
        changed_target = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                **payload,
                "request_id": "putaway-idempotency-2",
                "to_location_id": self.alternate_destination.pk,
            },
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertFalse(first.data["idempotent"])
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(changed_target.status_code, 400, changed_target.data)
        line.refresh_from_db()
        self.assertEqual(line.qty_done, Decimal("1.000"))
        self.assertEqual(TaskScanLog.objects.filter(task=task).count(), 1)

    def test_rejects_invalid_quantity_task_line_type_and_locations_without_writes(self):
        operator = self.operator("putaway-invalid-operator")
        task, line = self.make_task("PUT-INVALID")
        other_task, other_line = self.make_task("PUT-INVALID-OTHER")
        receive_task, receive_line = self.make_task(
            "PUT-WRONG-TYPE", task_type=WmsTask.TaskType.RECEIVE
        )
        client = self.client_for(operator)
        self.claim_and_start(client, task)
        self.claim_and_start(client, receive_task)

        cases = [
            ("putaway-invalid-zero", line.pk, self.destination.pk, "0.000", 400),
            ("putaway-invalid-negative", line.pk, self.destination.pk, "-1.000", 400),
            ("putaway-invalid-over", line.pk, self.destination.pk, "5.001", 400),
            ("putaway-invalid-source", line.pk, self.source.pk, "1.000", 400),
            ("putaway-invalid-frozen", line.pk, self.frozen_location.pk, "1.000", 404),
            (
                "putaway-invalid-disabled",
                line.pk,
                self.disabled_location.pk,
                "1.000",
                404,
            ),
            (
                "putaway-invalid-foreign",
                line.pk,
                self.foreign_location.pk,
                "1.000",
                404,
            ),
            (
                "putaway-invalid-otherline",
                other_line.pk,
                self.destination.pk,
                "1.000",
                404,
            ),
        ]
        for request_id, line_id, location_id, qty, expected_status in cases:
            with self.subTest(request_id=request_id):
                response = client.post(
                    f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
                    {
                        "request_id": request_id,
                        "line_id": line_id,
                        "to_location_id": location_id,
                        "qty": qty,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, expected_status, response.data)

        wrong_type = client.post(
            f"/api/inbound/pda/tasks/{receive_task.pk}/record-putaway/",
            {
                "request_id": "putaway-invalid-type",
                "line_id": receive_line.pk,
                "to_location_id": self.destination.pk,
                "qty": "1.000",
            },
            format="json",
        )
        self.assertEqual(wrong_type.status_code, 400, wrong_type.data)
        line.refresh_from_db()
        self.assertEqual(line.qty_done, Decimal("0"))
        self.assertFalse(TaskScanLog.objects.filter(task=task).exists())

    def test_second_operator_cannot_start_or_record_claimed_task(self):
        first_operator = self.operator("putaway-owner-1")
        second_operator = self.operator("putaway-owner-2")
        task, line = self.make_task("PUT-OWNERSHIP")
        first_client = self.client_for(first_operator)
        second_client = self.client_for(second_operator)
        claimed = first_client.post(f"/api/inbound/pda/tasks/{task.pk}/claim/")
        self.assertEqual(claimed.status_code, 200, claimed.data)
        self.assertEqual(
            second_client.post(f"/api/inbound/pda/tasks/{task.pk}/claim/").status_code,
            404,
        )
        self.assertEqual(
            second_client.post(f"/api/inbound/pda/tasks/{task.pk}/start/").status_code,
            404,
        )
        denied_record = second_client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                "request_id": "putaway-other-user",
                "line_id": line.pk,
                "to_location_id": self.destination.pk,
                "qty": "1.000",
            },
            format="json",
        )
        self.assertEqual(denied_record.status_code, 404, denied_record.data)
        self.assertFalse(TaskScanLog.objects.filter(task=task).exists())


@pytest.mark.integration
class PutawayTaskGenerationAndReviewTests(PutawayFixtureMixin, TestCase):
    def setUp(self):
        self.build_fixture()

    def test_generation_groups_multiple_receive_transactions_and_is_idempotent(self):
        receive_task = WmsTask.objects.create(
            task_no="PUT-RECEIVE-SOURCE",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.owner,
            warehouse=self.warehouse,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        transactions = [
            InventoryTransaction.objects.create(
                tx_type="RECEIVE",
                owner=self.owner,
                product=product,
                warehouse=self.warehouse,
                location=location,
                qty_delta=qty,
                src_model="WmsTask",
                src_id=receive_task.pk,
                src_line_id=index,
                src_no=receive_task.task_no,
            )
            for index, (product, location, qty) in enumerate(
                [
                    (self.product, self.source, Decimal("1.2500")),
                    (self.other_product, self.destination, Decimal("2.7500")),
                ],
                start=1,
            )
        ]
        transactions[0].batch_no = "PUT-LOT-001"
        transactions[0].production_date = datetime.date(2026, 8, 1)
        transactions[0].expiry_date = datetime.date(2027, 8, 1)
        transactions[0].serial_no = "PUT-SERIAL-001"
        transactions[0].save(
            update_fields=[
                "batch_no",
                "production_date",
                "expiry_date",
                "serial_no",
                "updated_at",
            ]
        )
        handler = DefaultPostingHandler()
        with mock.patch(
            "allapp.tasking.plugins.handlers.DocSequence.next_code",
            return_value="PUT-GENERATED-1",
        ):
            first = handler._create_putaway_task(receive_task, None, datetime.datetime.now())
            second = handler._create_putaway_task(receive_task, None, datetime.datetime.now())

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, WmsTask.Status.READY)
        self.assertEqual(first.lines.count(), 2)
        self.assertEqual(
            first.lines.get(src_id=transactions[0].pk).plan_meta,
            {
                "lot_no": "PUT-LOT-001",
                "mfg_date": "2026-08-01",
                "exp_date": "2027-08-01",
                "serial_no": "PUT-SERIAL-001",
            },
        )
        self.assertCountEqual(
            list(first.lines.values_list("product_id", "from_location_id", "qty_plan", "src_id")),
            [
                (self.product.pk, self.source.pk, Decimal("1.250"), transactions[0].pk),
                (
                    self.other_product.pk,
                    self.destination.pk,
                    Decimal("2.750"),
                    transactions[1].pk,
                ),
            ],
        )
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PUTAWAY,
                source_model="WmsTask",
                source_pk=str(receive_task.pk),
            ).count(),
            1,
        )

    def test_no_positive_receive_transactions_produce_no_putaway_task(self):
        receive_task = WmsTask.objects.create(
            task_no="PUT-RECEIVE-EMPTY",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.owner,
            warehouse=self.warehouse,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        InventoryTransaction.objects.create(
            tx_type="ISSUE",
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.source,
            qty_delta=Decimal("-1.0000"),
            src_model="WmsTask",
            src_id=receive_task.pk,
            src_line_id=1,
            src_no=receive_task.task_no,
        )
        result = DefaultPostingHandler()._create_putaway_task(
            receive_task, None, datetime.datetime.now()
        )
        self.assertIsNone(result)
        self.assertFalse(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PUTAWAY,
                source_model="WmsTask",
                source_pk=str(receive_task.pk),
            ).exists()
        )

    def test_release_complete_review_reject_and_approval_guards(self):
        operator = self.operator("putaway-workflow-operator")
        manager = self.manager()
        task, line = self.make_task("PUT-WORKFLOW", status=WmsTask.Status.READY)

        with self.assertRaises(DRFValidationError):
            approve_task(task.pk, by_user=manager)
        publish_task(task)
        task.refresh_from_db()
        self.assertEqual(task.status, WmsTask.Status.RELEASED)

        client = self.client_for(operator)
        self.claim_and_start(client, task)
        completed = client.post(
            f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
            {
                "request_id": "putaway-workflow-complete",
                "line_id": line.pk,
                "to_location_id": self.destination.pk,
                "qty": "5.000",
            },
            format="json",
        )
        self.assertEqual(completed.status_code, 200, completed.data)

        with self.assertRaises(PermissionDenied):
            approve_task(task.pk, by_user=operator)
        rejected = reject_task(task.pk, by_user=manager, note="Wrong shelf")
        self.assertEqual(rejected.review_status, WmsTask.ReviewStatus.REJECTED)
        self.assertEqual(rejected.posting_status, WmsTask.PostingStatus.NONE)

        WmsTask.objects.filter(pk=task.pk).update(
            review_status=WmsTask.ReviewStatus.PENDING,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        approved = approve_task(task.pk, by_user=manager, note="Verified")
        self.assertEqual(approved.review_status, WmsTask.ReviewStatus.APPROVED)
        self.assertEqual(approved.posting_status, WmsTask.PostingStatus.PENDING)

    def test_order_closes_only_after_all_sibling_putaway_tasks_are_posted(self):
        manager = self.manager("putaway-close-manager")
        supplier = Supplier.objects.create(
            owner=self.owner, code="PUT-SUP", name="Putaway supplier"
        )
        order = InboundOrder.objects.create(
            order_no="PUT-ORDER-CLOSE",
            owner=self.owner,
            warehouse=self.warehouse,
            supplier=supplier,
        )
        receive_task = WmsTask.objects.create(
            task_no="PUT-ORDER-RECEIVE",
            task_type=WmsTask.TaskType.RECEIVE,
            owner=self.owner,
            warehouse=self.warehouse,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(order.pk),
        )
        common = {
            "task_type": WmsTask.TaskType.PUTAWAY,
            "owner": self.owner,
            "warehouse": self.warehouse,
            "status": WmsTask.Status.COMPLETED,
            "review_status": WmsTask.ReviewStatus.APPROVED,
            "source_app": "tasking",
            "source_model": "WmsTask",
            "source_pk": str(receive_task.pk),
        }
        first = WmsTask.objects.create(
            task_no="PUT-ORDER-CHILD-1",
            posting_status=WmsTask.PostingStatus.POSTED,
            **common,
        )
        second = WmsTask.objects.create(
            task_no="PUT-ORDER-CHILD-2",
            posting_status=WmsTask.PostingStatus.PENDING,
            **common,
        )

        self.assertIsNone(close_inbound_order_after_putaway(first, by_user=manager))
        order.refresh_from_db()
        self.assertFalse(order.is_closed)
        WmsTask.objects.filter(pk=second.pk).update(posting_status=WmsTask.PostingStatus.POSTED)
        second.refresh_from_db()
        closed = close_inbound_order_after_putaway(second, by_user=manager)
        self.assertEqual(closed.pk, order.pk)
        order.refresh_from_db()
        self.assertTrue(order.is_closed)
        self.assertEqual(order.close_reason, "上架完成并已过账")


@skipUnless(
    connection.vendor == "mysql",
    "concurrent putaway submission is verified on production-like MySQL",
)
@pytest.mark.api
class PutawayPdaConcurrencyTests(PutawayFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.build_fixture()

    def test_two_requests_for_the_same_remaining_quantity_cannot_over_putaway(self):
        operator = self.operator("putaway-concurrent-operator")
        task, line = self.make_task("PUT-CONCURRENT", qty=Decimal("1.000"))
        client = self.client_for(operator)
        self.claim_and_start(client, task)
        barrier = threading.Barrier(2)
        statuses = []
        errors = []
        lock = threading.Lock()

        def invoke(index):
            close_old_connections()
            thread_client = self.client_for(self.user_model.objects.get(pk=operator.pk))
            try:
                barrier.wait(timeout=10)
                response = thread_client.post(
                    f"/api/inbound/pda/tasks/{task.pk}/record-putaway/",
                    {
                        "request_id": f"putaway-concurrent-{index}",
                        "line_id": line.pk,
                        "to_location_id": self.destination.pk,
                        "qty": "1.000",
                    },
                    format="json",
                )
                with lock:
                    statuses.append(response.status_code)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=invoke, args=(index,)) for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        if any(thread.is_alive() for thread in threads):
            self.fail("concurrent putaway requests did not finish")
        if errors:
            raise errors[0]

        self.assertEqual(len(statuses), 2)
        self.assertEqual(sum(status == 200 for status in statuses), 1)
        self.assertEqual(sum(status == 400 for status in statuses), 1)
        line.refresh_from_db()
        self.assertEqual(line.qty_done, Decimal("1.000"))
        self.assertEqual(TaskScanLog.objects.filter(task=task).count(), 1)
        extra = PutawayLineExtra.objects.get(line=line)
        self.assertEqual(extra.qty_moved, Decimal("1.000"))
