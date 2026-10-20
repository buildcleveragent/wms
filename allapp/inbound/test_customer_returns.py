"""Customer return workflow and inventory invariants against canonical MySQL."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import resolve
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.inventory import test_owner_detail as inventory_fixtures
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTaskLine

from . import customer_return_services as svc
from .customer_return_api import CustomerReturnViewSet, ReturnEligibilityView
from .customer_return_models import CustomerReturn, CustomerReturnCommand, CustomerReturnEvent


class CustomerReturnTests(TestCase):
    setUpTestData = classmethod(inventory_fixtures.OwnerInventoryDetailTests.setUpTestData.__func__)
    task = inventory_fixtures.OwnerInventoryDetailTests.task
    tx = inventory_fixtures.OwnerInventoryDetailTests.tx

    def setUp(self):
        self.files = TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        self.override = override_settings(PRIVATE_RETURN_ROOT=self.files.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        from .customer_return_models import CustomerReturnPhoto

        storage_patch = patch.object(
            CustomerReturnPhoto._meta.get_field("file"),
            "storage",
            FileSystemStorage(location=self.files.name),
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.grant(self.user, "apply_customer_return")
        self.manager = self.actor(
            "crm",
            "owner_manager",
            ["apply_customer_return", "approve_customer_return", "view_outboundorder"],
        )
        self.wh = self.actor("crw", "warehouse_manager", ["manage_customer_return"])
        self.op = self.actor(
            "cro", "warehouse_operator", ["receive_customer_return", "claim_task_as_wh_operator"]
        )
        self.order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            order_no="CR-ORIGINAL",
            created_by=self.user,
        )
        self.line = OutboundOrderLine.objects.create(
            order=self.order, product=self.product, base_uom=self.unit, base_qty=10
        )
        self.dispatch = self.task(
            "DISPATCH",
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="POSTED",
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(self.order.pk),
        )
        WmsTaskLine.objects.create(
            task=self.dispatch,
            product=self.product,
            qty_plan=10,
            qty_done=10,
            src_model="outbound.OutboundOrderLine",
            src_id=self.line.pk,
        )

    def grant(self, user, *codes):
        user.user_permissions.add(*Permission.objects.filter(codename__in=codes))
        for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(attr, None)

    def actor(self, name, role, perms):
        user = get_user_model().objects.create_user(
            username=name, owner=self.owner if role.startswith("owner") else None
        )
        UserRoleScope.objects.create(
            user=user,
            role=role,
            **(
                {"owner": self.owner} if role.startswith("owner") else {"warehouse": self.warehouse}
            ),
        )
        self.grant(user, *perms)
        return user

    def call(self, action, data=None, user=None, pk=None, method="post", **kwargs):
        request = getattr(APIRequestFactory(), method)("/", data or {}, format="json")
        force_authenticate(request, user=user or self.user)
        return CustomerReturnViewSet.as_view({method: action})(
            request, **({"pk": pk} if pk else {}), **kwargs
        )

    def draft(self, qty="5", user=None):
        response = self.call(
            "create",
            {
                "request_id": str(uuid4()),
                "original_order_id": self.order.pk,
                "lines": [
                    {
                        "original_line_id": self.line.pk,
                        "qty": qty,
                        "reason_code": "UNWANTED",
                        "reason": "客户取消需求",
                    }
                ],
            },
            user,
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def act(self, obj, operation, user=None, **extra):
        return self.call(
            "workflow",
            {"request_id": str(uuid4()), "version": obj["version"], **extra},
            user,
            obj["id"],
            operation=operation,
        )

    def ready(self, qty="5"):
        obj = self.draft(qty)
        for action, actor in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
            ("claim", self.op),
        ]:
            response = self.act(obj, action, actor)
            self.assertEqual(response.status_code, 200, response.data)
            obj = response.data
        return obj

    def receipt(self, obj, qty="2", **extra):
        return self.call(
            "receipts",
            {
                "request_id": str(uuid4()),
                "version": obj["version"],
                "lines": [
                    {
                        "line_id": obj["lines"][0]["id"],
                        "location_id": self.location.pk,
                        "batch_no": "CR-BATCH",
                        "qty_ok": qty,
                        **extra,
                    }
                ],
            },
            self.op,
            obj["id"],
        )

    def test_real_route(self):
        self.assertEqual(
            resolve(f"/api/outbound/orders/{self.order.pk}/return-eligibility/").func.view_class,
            ReturnEligibilityView,
        )

    def test_partial_complete_inventory_and_repeat_post(self):
        obj = self.ready()
        response = self.receipt(obj)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "PARTIAL")
        response = self.receipt(response.data, "3")
        self.assertEqual(response.data["status"], "COMPLETED")
        detail = InventoryDetail.objects.get(batch_no="CR-BATCH")
        self.assertEqual(detail.available_qty, 5)
        svc.post_receipt(self.op, response.data["receipts"][0]["id"])
        self.assertEqual(InventoryTransaction.objects.filter(memo="销售退货入库").count(), 2)
        self.assertEqual(svc.allowance(self.order)[0][0]["available_qty"], "5.0000")

    def test_manager_submission_skips_owner_review(self):
        obj = self.draft(user=self.manager)
        self.assertEqual(self.act(obj, "submit", self.manager).data["status"], "WHS_PENDING")

    def test_pending_reservation_rejection_and_resubmit(self):
        obj = self.draft("8")
        obj = self.act(obj, "submit").data
        second = self.draft("3")
        self.assertEqual(self.act(second, "submit").status_code, 400)
        obj = self.act(obj, "owner-reject", self.manager, reason="请修改数量").data
        self.assertEqual(Decimal(svc.allowance(self.order)[0][0]["available_qty"]), Decimal("10"))
        self.assertEqual(self.act(obj, "submit").status_code, 200)
        self.assertEqual(CustomerReturnEvent.objects.filter(action="owner-reject").count(), 1)

    def test_stale_version_and_idempotency(self):
        obj = self.draft()
        data = {"request_id": str(uuid4()), "version": obj["version"]}
        first = self.call("workflow", data, pk=obj["id"], operation="submit")
        again = self.call("workflow", data, pk=obj["id"], operation="submit")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(again.data["version"], first.data["version"])
        self.assertEqual(self.act(obj, "submit").status_code, 409)

    def test_other_salesperson_and_warehouse_isolation(self):
        obj = self.draft()
        other = self.actor(
            "cr-other", "owner_salesperson", ["apply_customer_return", "view_outboundorder"]
        )
        self.assertEqual(
            self.call("retrieve", user=other, pk=obj["id"], method="get").status_code, 404
        )
        UserRoleScope.objects.filter(user=self.wh).update(warehouse=self.warehouse2)
        self.assertEqual(
            self.call("retrieve", user=self.wh, pk=obj["id"], method="get").status_code, 404
        )

    def test_duplicate_product_requires_explicit_line_evidence(self):
        second = OutboundOrderLine.objects.create(
            order=self.order, product=self.product, base_uom=self.unit, base_qty=4
        )
        facts, _ = svc.allowance(self.order)
        self.assertEqual(
            next(r for r in facts if r["original_line_id"] == second.pk)["available_qty"], "0"
        )
        WmsTaskLine.objects.filter(task=self.dispatch).update(src_model="", src_id=None)
        self.assertTrue(all(not r["can_apply"] for r in svc.allowance(self.order)[0]))

    def test_assisted_uses_actual_issue_not_dispatch(self):
        OutboundOrder.objects.filter(pk=self.order.pk).update(processing_mode="WAREHOUSE_ASSISTED")
        self.order.refresh_from_db()
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])
        self.tx(
            "-3",
            kind="ISSUE",
            src_model="outbound.OutboundOrder",
            src_id=self.order.pk,
            src_line_id=self.line.pk,
        )
        self.assertEqual(svc.allowance(self.order)[0][0]["fulfilled_qty"], "3.0000")

    def test_cancelled_dispatch_not_eligible(self):
        self.dispatch.status = "CANCELLED"
        self.dispatch.review_status = "NOT_READY"
        self.dispatch.posting_status = "NOT_READY"
        self.dispatch.save(update_fields=["status", "review_status", "posting_status"])
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])

    def test_close_remaining_keeps_stock(self):
        obj = self.receipt(self.ready()).data
        response = self.act(obj, "close", self.wh, reason="剩余未寄回")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "CLOSED")
        self.assertEqual(InventoryDetail.objects.get(batch_no="CR-BATCH").onhand_qty, 2)
        self.assertEqual(svc.allowance(self.order)[0][0]["available_qty"], "8.0000")

    def test_receipt_cannot_exceed_plan_or_use_other_warehouse(self):
        obj = self.ready()
        self.assertEqual(self.receipt(obj, "6").status_code, 400)
        self.assertEqual(self.receipt(obj, location_id=self.remote.pk).status_code, 400)
        self.assertFalse(InventoryTransaction.objects.exists())

    def test_damage_requires_evidence(self):
        obj = self.ready()
        self.assertEqual(self.receipt(obj, "0", qty_damage="1", reason="破损").status_code, 400)

    def upload(self, obj, purpose="RECEIPT", user=None, content=None):
        if content is None:
            data = BytesIO()
            Image.new("RGB", (10, 10)).save(data, format="PNG")
            content = data.getvalue()
        request = APIRequestFactory().post(
            "/",
            {
                "line_id": obj["lines"][0]["id"],
                "purpose": purpose,
                "file": SimpleUploadedFile("photo.png", content, content_type="image/png"),
            },
            format="multipart",
        )
        force_authenticate(request, user=user or self.op)
        return CustomerReturnViewSet.as_view({"post": "photos"})(request, pk=obj["id"])

    def test_damage_and_reject_quantities_and_private_photos(self):
        obj = self.ready()
        photo = self.upload(obj)
        self.assertEqual(photo.status_code, 201, photo.data)
        response = self.receipt(
            obj,
            "1",
            qty_damage="2",
            qty_reject="2",
            reason="破损拒收",
            photo_ids=[photo.data["id"]],
        )
        self.assertEqual(response.status_code, 200, response.data)
        detail = InventoryDetail.objects.get(batch_no="CR-BATCH")
        self.assertEqual((detail.onhand_qty, detail.available_qty, detail.damaged_qty), (3, 1, 2))
        self.assertEqual(svc.allowance(self.order)[0][0]["available_qty"], "7.0000")
        self.assertEqual(
            self.call(
                "photo", user=self.op, pk=obj["id"], photo_id=photo.data["id"], method="delete"
            ).status_code,
            403,
        )
        other = self.actor("cr-photo-other", "owner_salesperson", ["view_outboundorder"])
        self.assertEqual(
            self.call(
                "photo", user=other, pk=obj["id"], photo_id=photo.data["id"], method="get"
            ).status_code,
            404,
        )

    def test_invalid_photo_and_missing_required_application_photo(self):
        obj = self.draft()
        CustomerReturn.objects.get(pk=obj["id"]).lines.update(reason_code="DAMAGE")
        self.assertEqual(self.act(obj, "submit").status_code, 400)
        self.assertEqual(self.upload(obj, "APPLICATION", self.user, b"fake").status_code, 400)
        self.assertEqual(self.upload(obj, "APPLICATION", self.user).status_code, 201)
        self.assertEqual(self.act(obj, "submit").status_code, 200)

    def test_audit_failure_rolls_back_submission(self):
        obj = self.draft()
        count = CustomerReturnCommand.objects.count()
        with patch.object(svc, "record_audit_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                svc.command(
                    self.user,
                    "submit",
                    {"request_id": uuid4(), "version": obj["version"]},
                    obj["id"],
                )
        self.assertEqual(CustomerReturn.objects.get(pk=obj["id"]).status, "DRAFT")
        self.assertEqual(CustomerReturnCommand.objects.count(), count)

    def test_failed_post_keeps_receipt_and_retries_once(self):
        obj = self.ready()
        with patch.object(svc, "_refresh_summaries", side_effect=svc.ValidationError("failure")):
            response = self.receipt(obj, "5")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "POSTING")
        self.assertFalse(InventoryTransaction.objects.exists())
        receipt_id = response.data["receipts"][0]["id"]
        self.assertTrue(svc.post_receipt(self.op, receipt_id))
        self.assertTrue(svc.post_receipt(self.op, receipt_id))
        self.assertEqual(InventoryTransaction.objects.count(), 1)

    def test_wrong_source_and_cross_warehouse_tasks_do_not_grant_allowance(self):
        WmsTaskLine.objects.filter(task=self.dispatch).update(src_id=999999)
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])
        self.dispatch.warehouse = self.warehouse2
        self.dispatch.save(update_fields=["warehouse"])
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])

    def test_task_line_cycle_is_rejected(self):
        line = WmsTaskLine.objects.get(task=self.dispatch)
        WmsTaskLine.objects.filter(pk=line.pk).update(
            src_model="tasking.WmsTaskLine", src_id=line.pk
        )
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])

    def test_pos_sales_keep_existing_return_channel(self):
        from allapp.pos.models import PosSale, PosSaleOrder

        sale = PosSale.objects.create(sale_no="CR-POS", warehouse=self.warehouse)
        PosSaleOrder.objects.create(
            sale=sale, owner=self.owner, outbound_order=self.order, amount=0
        )
        facts, _ = svc.allowance(self.order)
        self.assertFalse(facts[0]["can_apply"])
        self.assertIn("POS", facts[0]["reason"])

    def test_permission_revocation_blocks_review_and_receipt(self):
        obj = self.act(self.draft(), "submit").data
        self.manager.user_permissions.remove(
            Permission.objects.get(codename="approve_customer_return")
        )
        self.grant(self.manager)
        response = self.act(obj, "owner-approve", self.manager)
        self.assertIn(response.status_code, [403, 409])
        self.assertEqual(CustomerReturn.objects.get(pk=obj["id"]).status, "OWNER_PENDING")

    def test_evidence_revision_preserves_original_quantity_and_reason(self):
        obj = self.act(self.draft("5"), "submit").data
        obj = self.act(obj, "owner-reject", self.manager, reason="修改数量").data
        response = self.call(
            "partial_update",
            {
                "request_id": str(uuid4()),
                "version": obj["version"],
                "lines": [
                    {
                        "id": obj["lines"][0]["id"],
                        "original_line_id": self.line.pk,
                        "qty": "2",
                        "reason_code": "OTHER",
                        "reason": "修订原因",
                    }
                ],
            },
            pk=obj["id"],
            method="patch",
        )
        self.assertEqual(response.status_code, 200, response.data)
        old = CustomerReturnEvent.objects.get(order_id=obj["id"], action="submit")
        self.assertEqual(old.snapshot["lines"][0]["qty"], "5.0000")
        self.assertEqual(old.snapshot["lines"][0]["reason"], "客户取消需求")

    def test_posting_audit_failure_preserves_receipt_and_rolls_back_inventory(self):
        obj = self.ready()
        actual = svc.record_audit_event

        def fail_post(**kwargs):
            if kwargs["action"].endswith(".post"):
                raise RuntimeError("audit unavailable")
            return actual(**kwargs)

        with patch.object(svc, "record_audit_event", side_effect=fail_post):
            response = self.receipt(obj, "5")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(InventoryTransaction.objects.exists())
        self.assertEqual(response.data["status"], "POSTING")
        self.assertTrue(svc.post_receipt(self.op, response.data["receipts"][0]["id"]))

    def test_serial_returns_restore_zero_balance_without_duplicate_stock(self):
        from allapp.products.models import Product

        Product.objects.filter(pk=self.product.pk).update(serial_control=True)
        self.product.refresh_from_db()
        self.tx(
            "-1",
            kind="ISSUE",
            src_model="outbound.OutboundOrder",
            src_id=self.order.pk,
            src_line_id=self.line.pk,
            serial_no="CR-SN",
            batch_no="CR-BATCH",
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            location=self.location,
            subwarehouse=self.sub,
            onhand_qty=0,
            serial_no="CR-SN",
            batch_no="CR-BATCH",
        )
        obj = self.ready("1")
        response = self.receipt(obj, "1", serial_no="CR-SN", location_id=self.location2.pk)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "COMPLETED")
        self.assertEqual(InventoryDetail.objects.filter(serial_no_norm="CR-SN").count(), 1)
        self.assertEqual(
            InventoryDetail.objects.get(serial_no_norm="CR-SN").location_id, self.location2.pk
        )

    def test_read_endpoints_do_not_write_and_transactions_resolve_return(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from allapp.inventory.models import InventorySummary
        from allapp.inventory.owner_detail import build_inventory_transactions

        obj = self.receipt(self.ready(), "5").data
        with CaptureQueriesContext(connection) as captured:
            self.call("retrieve", pk=obj["id"], method="get")
            svc.allowance(self.order)
        self.assertFalse(
            any(
                q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for q in captured
            )
        )
        summary = InventorySummary.objects.select_related("product").get(
            owner=self.owner, product=self.product
        )
        result = build_inventory_transactions(summary, {"page_size": 50}, self.user)
        self.assertIn("销售退货单", str(result))

    @override_settings(INVENTORY_FIFO_ENABLED=True)
    def test_missing_cost_is_marked_and_quantity_layer_matches_receipt(self):
        from allapp.inventory.models import InventoryCostLayer, InventoryLayerPosition

        response = self.receipt(self.ready(), "5")
        self.assertEqual(response.data["status"], "COMPLETED", response.data)
        layer = InventoryCostLayer.objects.get(source_type="CUSTOMER_RETURN")
        self.assertEqual(layer.cost_quality, "COST_MISSING")
        self.assertIsNone(layer.unit_cost)
        self.assertEqual(InventoryLayerPosition.objects.get(layer=layer).remaining_qty, 5)

    def test_cross_owner_order_and_mixed_unit_are_not_eligible(self):
        from allapp.products.models import ProductUom

        other = self.actor(
            "foreign-owner", "owner_salesperson", ["apply_customer_return", "view_outboundorder"]
        )
        UserRoleScope.objects.filter(user=other).update(owner=self.other)
        get_user_model().objects.filter(pk=other.pk).update(owner=self.other)
        other.refresh_from_db()
        with self.assertRaises(svc.NotFound):
            svc.original_for(other, self.order.pk)
        unit = ProductUom.objects.create(code="CR-BOX", name="箱")
        OutboundOrderLine.objects.filter(pk=self.line.pk).update(base_uom=unit)
        self.assertFalse(svc.allowance(self.order)[0][0]["can_apply"])

    def test_admin_photo_uses_authenticated_session_and_rejects_anonymous_access(self):
        from django.test import Client

        obj = self.draft()
        photo = self.upload(obj, "APPLICATION", self.user).data
        client = Client()
        self.assertEqual(client.get(photo["url"]).status_code, 401)
        client.force_login(self.wh)
        response = client.get(photo["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        # Let Django's test client exhaust/close the streaming response while it
        # suspends request_finished connection cleanup inside the outer TestCase transaction.
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\xff\xd8"))

    def test_private_storage_cannot_be_configured_inside_public_media(self):
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        from .customer_return_models import private_return_storage

        with override_settings(PRIVATE_RETURN_ROOT=str(settings.MEDIA_ROOT) + "/returns"):
            with self.assertRaises(ImproperlyConfigured):
                private_return_storage()


class CustomerReturnConcurrencyTests(TransactionTestCase):
    grant = CustomerReturnTests.grant
    actor = CustomerReturnTests.actor
    call = CustomerReturnTests.call
    draft = CustomerReturnTests.draft
    task = CustomerReturnTests.task
    tx = CustomerReturnTests.tx

    def setUp(self):
        inventory_fixtures.OwnerInventoryDetailTests.setUpTestData.__func__(type(self))
        CustomerReturnTests.setUp(self)

    def test_two_simultaneous_requests_cannot_reserve_more_than_shipped(self):
        first, second = self.draft("7"), self.draft("7")
        barrier = Barrier(2)
        user_id = self.user.pk

        def worker(obj):
            connections.close_all()
            try:
                user = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=10)
                try:
                    svc.command(
                        user,
                        "submit",
                        {"request_id": uuid4(), "version": obj["version"]},
                        obj["id"],
                    )
                    return "accepted"
                except svc.ValidationError:
                    return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, [first, second]))
        self.assertCountEqual(results, ["accepted", "rejected"])
        self.assertEqual(CustomerReturn.objects.filter(status="OWNER_PENDING").count(), 1)
