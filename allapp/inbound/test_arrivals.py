"""Real MySQL contracts for owner ASN batches, approval and classified stock."""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Supplier
from allapp.inventory import test_owner_detail as inventory_fixtures
from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.tasking.models import WmsTask

from .arrival_api import ArrivalOrderViewSet
from .models import InboundOrder


class ArrivalTests(TestCase):
    setUpTestData = classmethod(inventory_fixtures.OwnerInventoryDetailTests.setUpTestData.__func__)

    def grant(self, user, *codes):
        user.user_permissions.add(*Permission.objects.filter(codename__in=codes))
        for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(attr, None)

    def actor(self, name, role, *perms):
        u = get_user_model().objects.create_user(
            username=name, owner=self.owner if role.startswith("owner") else None
        )
        UserRoleScope.objects.create(
            user=u,
            role=role,
            **(
                {"owner": self.owner} if role.startswith("owner") else {"warehouse": self.warehouse}
            ),
        )
        self.grant(u, *perms)
        return u

    def setUp(self):
        type(self.product).objects.filter(pk=self.product.pk).update(
            batch_control=False, expiry_control=False
        )
        self.product.refresh_from_db()
        self.grant(
            self.user,
            "view_inboundorder",
            "add_inboundorder",
            "change_inboundorder",
            "submit_as_owner_buyers",
        )
        self.manager = self.actor(
            "asn-om", "owner_manager", "view_inboundorder", "approve_as_owner_manager"
        )
        self.wh = self.actor(
            "asn-wh",
            "warehouse_manager",
            "manage_arrival",
            "view_inboundorder",
            "approve_as_wh_manager",
            "taskconfirm_as_wh_manager",
        )
        self.op = self.actor(
            "asn-op", "warehouse_operator", "claim_task_as_wh_operator", "view_wmstask"
        )
        self.supplier = Supplier.objects.create(owner=self.owner, code="ASN-S", name="供应商")

    def call(self, action, data=None, user=None, pk=None, method="post", **kwargs):
        r = getattr(APIRequestFactory(), method)("/?owner_portal=1", data or {}, format="json")
        force_authenticate(r, user=user or self.user)
        return ArrivalOrderViewSet.as_view({method: action})(
            r, **({"pk": str(pk)} if pk else {}), **kwargs
        )

    def draft(self, qty="10", price="2.5000"):
        response = self.call(
            "create",
            {
                "request_id": str(uuid4()),
                "receipt_mode": "BATCH",
                "supplier_id": self.supplier.pk,
                "warehouse_id": self.warehouse.pk,
                "biz_date": "2026-10-20",
                "eta": "2026-10-25T09:00:00+08:00",
                "delivery_contact": "送货人",
                "delivery_phone": "13800000000",
                "lines": [{"product_id": self.product.pk, "base_qty": qty, "base_price": price}],
            },
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_purchase_reference_is_optional_through_submission_and_approval(self):
        for price in (None, Decimal("12.5000")):
            type(self.product).objects.filter(pk=self.product.pk).update(purchase_price=price)
            response = self.call("arrival_options", method="get")
            self.assertEqual(response.status_code, 200, response.data)
            product = next(row for row in response.data["results"] if row["id"] == self.product.pk)
            if price is None:
                self.assertIsNone(product["purchase_price"])
            else:
                self.assertEqual(Decimal(product["purchase_price"]), price)
        type(self.product).objects.filter(pk=self.product.pk).update(purchase_price=None)
        obj = self.draft(price="0")
        for action, actor in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
        ]:
            obj = self.act(obj, action, actor)
        self.assertEqual(Decimal(obj["lines"][0]["base_price"]), 0)

    def act(self, obj, action, actor=None, **extra):
        r = self.call(
            "arrival_action",
            {"request_id": str(uuid4()), "version": obj["version"], **extra},
            user=actor,
            pk=obj["id"],
            operation=action,
        )
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def batch(self, obj, action, actor=None, index=-1, **extra):
        receipt = obj["batches"][index]
        r = self.call(
            "batch_action",
            {"request_id": str(uuid4()), "version": receipt["version"], **extra},
            user=actor or self.op,
            pk=obj["id"],
            receipt_id=str(receipt["id"]),
            operation=action,
        )
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def ready(self, qty="10"):
        obj = self.draft(qty)
        for action, actor in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
        ]:
            obj = self.act(obj, action, actor)
        self.assertEqual(WmsTask.objects.get(pk=obj["batches"][0]["task_id"]).status, "RELEASED")
        return self.batch(obj, "claim")

    def receive(self, obj, ok="3", damage="1", reject="1", **extra):
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": ok,
                    "qty_damage": damage,
                    "qty_reject": reject,
                    "damage_reason": "运输破损",
                    "reject_reason": "包装不符",
                    "lot_no": "LOT-A",
                    **extra,
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        obj = self.batch(obj, "batch-approve", self.wh)
        self.assertFalse(obj["batches"][-1]["posting_error"], obj)
        return obj

    def putaway(self, obj):
        obj = self.batch(obj, "putaway-claim")
        obj = self.batch(
            obj,
            "putaway-record",
            putaway_lines=[
                {"id": r["id"], "location_id": self.location2.pk, "qty": r["qty_plan"]}
                for r in obj["batches"][-1]["putaway_lines"]
            ],
        )
        return self.batch(obj, "putaway-approve", self.wh)

    @override_settings(INVENTORY_FIFO_ENABLED=True)
    def test_full_batch_damage_and_later_delivery(self):
        obj = self.receive(self.ready())
        self.assertIsNotNone(WmsTask.objects.get(pk=obj["batches"][0]["task_id"]).started_at)
        row = InventoryDetail.objects.get(location=self.location, onhand_qty__gt=0)
        self.assertEqual((row.onhand_qty, row.available_qty, row.damaged_qty), (4, 3, 1))
        obj = self.putaway(obj)
        self.assertIsNotNone(
            WmsTask.objects.get(pk=obj["batches"][0]["putaway_task_id"]).started_at
        )
        self.assertFalse(obj["is_closed"])
        row = InventoryDetail.objects.get(location=self.location2, onhand_qty__gt=0)
        self.assertEqual((row.onhand_qty, row.available_qty, row.damaged_qty), (4, 3, 1))
        obj = self.act(obj, "batch-create", self.wh)
        obj = self.batch(obj, "claim")
        obj = self.receive(obj, "5", "0", "0")
        obj = self.putaway(obj)
        self.assertTrue(obj["is_closed"])
        self.assertEqual(Decimal(obj["lines"][0]["posted_qty"]), 9)
        self.assertEqual(Decimal(obj["lines"][0]["putaway_qty"]), 9)
        self.assertEqual(
            InventorySummary.objects.get(owner=self.owner, product=self.product).available_qty, 8
        )

    def test_all_rejected_has_no_stock_or_putaway(self):
        obj = self.receive(self.ready("2"), "0", "0", "2")
        self.assertTrue(obj["is_closed"])
        self.assertEqual(obj["batches"][0]["stage"], "NO_STOCK")
        self.assertFalse(InventoryTransaction.objects.exists())
        self.assertIsNone(obj["batches"][0]["putaway_task_id"])

    def test_close_zero_and_short_receipt(self):
        obj = self.draft()
        for action, actor in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
        ]:
            obj = self.act(obj, action, actor)
        obj = self.act(obj, "close", self.wh, reason="供应商取消送货")
        self.assertTrue(obj["is_closed"])
        self.assertEqual(Decimal(obj["lines"][0]["short_qty"]), 10)
        self.assertFalse(InventoryTransaction.objects.exists())

    def test_audit_failure_rolls_back_inventory_and_retry_is_idempotent(self):
        obj = self.ready()
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "10",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        with patch(
            "allapp.inbound.arrival_posting.event", side_effect=RuntimeError("audit unavailable")
        ):
            obj = self.batch(obj, "batch-approve", self.wh)
        self.assertTrue(obj["batches"][0]["posting_error"])
        self.assertFalse(InventoryTransaction.objects.exists())
        self.assertFalse(InventoryDetail.objects.exists())
        closing = self.call(
            "arrival_action",
            {"request_id": str(uuid4()), "version": obj["version"], "reason": "仍有入账失败批次"},
            self.wh,
            obj["id"],
            operation="close",
        )
        self.assertEqual(closing.status_code, 409)
        receipt = obj["batches"][0]
        retry = {"request_id": str(uuid4()), "version": receipt["version"]}
        for _ in range(2):
            response = self.call(
                "batch_action",
                retry,
                self.wh,
                obj["id"],
                receipt_id=receipt["id"],
                operation="retry-posting",
            )
            self.assertEqual(response.status_code, 200, response.data)
            obj = response.data
        self.assertFalse(obj["batches"][0]["posting_error"])
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        self.assertEqual(WmsTask.objects.filter(task_type="PUTAWAY").count(), 1)
        self.wh.groups.clear()
        self.wh.user_permissions.remove(Permission.objects.get(codename="manage_arrival"))
        revoked = get_user_model().objects.get(pk=self.wh.pk)
        response = self.call(
            "batch_action",
            retry,
            revoked,
            obj["id"],
            receipt_id=receipt["id"],
            operation="retry-posting",
        )
        self.assertEqual(response.status_code, 403)

    def test_overage_needs_reason_and_explicit_manager_approval(self):
        obj = self.ready("2")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "3",
                }
            ],
        )
        receipt = obj["batches"][0]
        bad = self.call(
            "batch_action",
            {"request_id": str(uuid4()), "version": receipt["version"]},
            self.op,
            obj["id"],
            receipt_id=receipt["id"],
            operation="batch-submit",
        )
        self.assertEqual(bad.status_code, 400)
        obj = self.batch(
            obj,
            "batch-edit",
            reason="供应商多送一件",
            lines=[
                {
                    "id": receipt["lines"][0]["id"],
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "3",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        receipt = obj["batches"][0]
        bad = self.call(
            "batch_action",
            {"request_id": str(uuid4()), "version": receipt["version"]},
            self.wh,
            obj["id"],
            receipt_id=receipt["id"],
            operation="batch-approve",
        )
        self.assertEqual(bad.status_code, 400)
        obj = self.batch(obj, "batch-approve", self.wh, approve_overage=True)
        self.assertEqual(Decimal(obj["lines"][0]["over_qty"]), 1)
        self.assertEqual(Decimal(obj["lines"][0]["base_qty"]), 2)

    def test_location_disabled_after_submission_blocks_posting_and_keeps_evidence_for_retry(self):
        obj = self.ready("1")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "1",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        type(self.location).objects.filter(pk=self.location.pk).update(is_frozen=True)
        obj = self.batch(obj, "batch-approve", self.wh)
        self.assertTrue(obj["batches"][0]["posting_error"])
        self.assertEqual(obj["batches"][0]["stage"], "APPROVED")
        self.assertFalse(InventoryTransaction.objects.exists())
        type(self.location).objects.filter(pk=self.location.pk).update(is_frozen=False)
        obj = self.batch(obj, "retry-posting", self.wh)
        obj = self.batch(obj, "putaway-claim")
        row = obj["batches"][0]["putaway_lines"][0]
        obj = self.batch(
            obj,
            "putaway-record",
            putaway_lines=[{"id": row["id"], "qty": "1", "location_id": self.location2.pk}],
        )
        type(self.location2).objects.filter(pk=self.location2.pk).update(is_disabled=True)
        obj = self.batch(obj, "putaway-approve", self.wh)
        self.assertTrue(obj["batches"][0]["posting_error"])
        self.assertFalse(obj["is_closed"])
        self.assertEqual(InventoryTransaction.objects.count(), 1)
        type(self.location2).objects.filter(pk=self.location2.pk).update(is_disabled=False)
        obj = self.batch(obj, "retry-posting", self.wh)
        self.assertTrue(obj["is_closed"])
        self.assertEqual(
            InventoryDetail.objects.get(onhand_qty__gt=0).location_id, self.location2.pk
        )

    def test_reject_edit_resubmit_retains_events_and_original_rows(self):
        obj = self.draft()
        obj = self.act(obj, "submit")
        obj = self.act(obj, "owner-reject", self.manager, reason="补充预计到货时间")
        obj = self.act(obj, "submit")
        obj = self.act(obj, "owner-approve", self.manager)
        obj = self.act(obj, "warehouse-confirm", self.wh)
        obj = self.batch(obj, "claim")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "1",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        obj = self.batch(obj, "batch-reject", self.wh, reason="请重新清点")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "id": obj["batches"][0]["lines"][0]["id"],
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "2",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        snapshots = [e for e in obj["events"] if e["action"] == "提交本批验收"]
        self.assertEqual([Decimal(e["lines"][0]["qty_ok"]) for e in snapshots], [1, 2])

    def test_permissions_and_legacy_snapshot_write_are_closed(self):
        from .views import InboundTaskViewSet

        obj = self.ready()
        outsider = self.actor("asn-other", "owner_salesperson", "view_inboundorder")
        self.assertEqual(self.call("progress", {}, outsider, obj["id"], "get").status_code, 404)
        self.assertEqual(
            self.call(
                "arrival_action",
                {"request_id": str(uuid4()), "version": obj["version"]},
                self.op,
                obj["id"],
                operation="close",
            ).status_code,
            403,
        )
        r = APIRequestFactory().post("/", {"lines": []}, format="json")
        force_authenticate(r, self.op)
        response = InboundTaskViewSet.as_view({"post": "record_receipt"})(
            r, pk=obj["batches"][0]["task_id"]
        )
        self.assertEqual(response.status_code, 409)
        bad = self.call(
            "batch_action",
            {
                "request_id": str(uuid4()),
                "version": obj["batches"][0]["version"],
                "lines": [
                    {
                        "order_line_id": obj["lines"][0]["id"],
                        "location_id": self.remote.pk,
                        "qty_ok": "1",
                    }
                ],
            },
            self.op,
            obj["id"],
            receipt_id=obj["batches"][0]["id"],
            operation="batch-edit",
        )
        self.assertEqual(bad.status_code, 400)

    def test_pending_batch_prevents_closing_and_damaged_stock_stays_unavailable(self):
        obj = self.ready("4")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_damage": "2",
                    "damage_reason": "外箱破损",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        bad = self.call(
            "arrival_action",
            {"request_id": str(uuid4()), "version": obj["version"], "reason": "未到货结束"},
            self.wh,
            obj["id"],
            operation="close",
        )
        self.assertEqual(bad.status_code, 409)
        obj = self.batch(obj, "batch-approve", self.wh)
        obj = self.act(obj, "close", self.wh, reason="剩余两件取消")
        self.assertFalse(obj["is_closed"])
        obj = self.putaway(obj)
        self.assertTrue(obj["is_closed"])
        self.assertEqual(Decimal(obj["lines"][0]["short_qty"]), 2)
        stock = InventoryDetail.objects.get(location=self.location2, onhand_qty__gt=0)
        self.assertEqual((stock.available_qty, stock.damaged_qty), (0, 2))

    def test_duplicate_rows_lots_and_pda_task_association(self):
        from .models import InboundOrderLine
        from .views import InboundTaskViewSet

        obj = self.draft()
        order = InboundOrder.objects.get(pk=obj["id"])
        line = InboundOrderLine.objects.create(order=order, product=self.product, base_qty=2)
        for a, u in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
        ]:
            obj = self.act(obj, a, u)
        request = APIRequestFactory().get("/", {"task_type": "RECEIVE"})
        force_authenticate(request, self.op)
        tasks = InboundTaskViewSet.as_view({"get": "list"})(request)
        self.assertEqual(tasks.status_code, 200, tasks.data)
        self.assertEqual(tasks.data["results"][0]["arrival"]["order_id"], obj["id"])
        obj = self.batch(obj, "claim")
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "1.250",
                    "lot_no": "A",
                },
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location2.pk,
                    "qty_ok": "2.750",
                    "lot_no": "B",
                },
                {
                    "order_line_id": line.pk,
                    "location_id": self.location.pk,
                    "qty_ok": "2",
                    "lot_no": "C",
                },
            ],
        )
        obj = self.batch(obj, "batch-submit")
        obj = self.batch(obj, "batch-approve", self.wh)
        self.assertEqual([Decimal(r["qty_ok"]) for r in obj["lines"]], [4, 2])
        self.assertEqual(InventoryTransaction.objects.filter(tx_type="RECEIVE").count(), 3)

    def test_photo_is_optional_and_private_and_fake_content_rejected(self):
        from io import BytesIO
        from tempfile import TemporaryDirectory

        from django.core.files.storage import FileSystemStorage
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from .arrival_models import ArrivalPhoto

        obj = self.ready()
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_damage": "1",
                    "damage_reason": "外包装破损",
                }
            ],
        )
        batch = obj["batches"][0]

        def upload(content):
            request = APIRequestFactory().post(
                "/",
                {
                    "request_id": str(uuid4()),
                    "version": batch["version"],
                    "line_id": batch["lines"][0]["id"],
                    "file": SimpleUploadedFile("damage.png", content, content_type="image/png"),
                },
                format="multipart",
            )
            force_authenticate(request, self.op)
            return ArrivalOrderViewSet.as_view({"post": "upload_photo"})(
                request, pk=obj["id"], receipt_id=batch["id"]
            )

        self.assertEqual(upload(b"not an image").status_code, 400)
        with (
            TemporaryDirectory() as folder,
            patch.object(
                ArrivalPhoto._meta.get_field("file"), "storage", FileSystemStorage(location=folder)
            ),
        ):
            content = BytesIO()
            Image.new("RGB", (3, 3)).save(content, format="PNG")
            response = upload(content.getvalue())
            self.assertEqual(response.status_code, 201, response.data)
            photo = response.data["batches"][0]["lines"][0]["photos"][0]
            outsider = self.actor("photo-outside", "owner_salesperson", "view_inboundorder")
            r = self.call(
                "photo",
                {},
                outsider,
                obj["id"],
                method="get",
                receipt_id=batch["id"],
                photo_id=photo["id"],
            )
            self.assertEqual(r.status_code, 404)
            obj = self.batch(response.data, "batch-submit")
            r = self.call(
                "photo",
                {"request_id": str(uuid4()), "version": obj["batches"][0]["version"]},
                self.op,
                obj["id"],
                method="delete",
                receipt_id=batch["id"],
                photo_id=photo["id"],
            )
            self.assertEqual(r.status_code, 409)

    def test_progress_reads_are_batched_and_notes_require_warehouse_action_permission(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        obj = self.ready("30")
        receipt = obj["batches"][0]
        denied = self.call(
            "batch_action",
            {
                "request_id": str(uuid4()),
                "version": receipt["version"],
                "reason": "仅查看账号不能写入处理记录",
            },
            self.user,
            obj["id"],
            receipt_id=receipt["id"],
            operation="note",
        )
        self.assertEqual(denied.status_code, 403)
        row = {
            "order_line_id": obj["lines"][0]["id"],
            "location_id": self.location.pk,
            "qty_ok": "1",
        }
        obj = self.batch(obj, "batch-edit", lines=[row])
        with CaptureQueriesContext(connection) as small:
            response = self.call("progress", {}, self.user, obj["id"], method="get")
        self.assertEqual(response.status_code, 200)
        obj = self.batch(obj, "batch-edit", lines=[{**row, "lot_no": str(i)} for i in range(25)])
        with CaptureQueriesContext(connection) as large:
            response = self.call("progress", {}, self.user, obj["id"], method="get")
        self.assertEqual(len(response.data["batches"][0]["lines"]), 25)
        self.assertLessEqual(len(large), len(small) + 2)
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in large))

    def test_serial_and_expiry_controls_and_putaway_preserve_evidence(self):
        type(self.product).objects.filter(pk=self.product.pk).update(
            serial_control=True, batch_control=True, expiry_control=True
        )
        obj = self.ready("1")
        batch = obj["batches"][0]
        line = {
            "order_line_id": obj["lines"][0]["id"],
            "location_id": self.location.pk,
            "qty_damage": "1",
            "damage_reason": "运输损坏",
        }
        for extra in [
            {},
            {"serial_no": "SER-1"},
            {"serial_no": "SER-1", "lot_no": "LOT-1"},
            {
                "serial_no": "SER-1",
                "lot_no": "LOT-1",
                "exp_date": "2027-01-01",
                "mfg_date": "2027-02-01",
            },
        ]:
            response = self.call(
                "batch_action",
                {
                    "request_id": str(uuid4()),
                    "version": batch["version"],
                    "lines": [{**line, **extra}],
                },
                self.op,
                obj["id"],
                receipt_id=batch["id"],
                operation="batch-edit",
            )
            self.assertEqual(response.status_code, 400, response.data)
        obj = self.receive(
            obj, "0", "1", "0", serial_no="ser-1", mfg_date="2026-10-01", exp_date="2027-10-01"
        )
        obj = self.putaway(obj)
        self.assertTrue(obj["is_closed"])
        stock = InventoryDetail.objects.get(onhand_qty__gt=0)
        self.assertEqual(
            (stock.serial_no, stock.location_id, stock.available_qty),
            ("SER-1", self.location2.pk, 0),
        )
        self.assertEqual(str(stock.expiry_date), "2027-10-01")
        other = self.ready("1")
        other = self.batch(
            other,
            "batch-edit",
            lines=[
                {
                    **line,
                    "order_line_id": other["lines"][0]["id"],
                    "serial_no": "SER-1",
                    "lot_no": "LOT-A",
                    "exp_date": "2027-10-01",
                }
            ],
        )
        receipt = other["batches"][0]
        response = self.call(
            "batch_action",
            {"request_id": str(uuid4()), "version": receipt["version"]},
            self.op,
            other["id"],
            receipt_id=receipt["id"],
            operation="batch-submit",
        )
        self.assertEqual(response.status_code, 400, response.data)

    @override_settings(INVENTORY_FIFO_ENABLED=True)
    def test_putaway_moves_its_own_cost_layer_when_batch_number_is_reused(self):
        from allapp.inventory.models import InventoryLayerPosition

        first = self.receive(self.ready("1"), "1", "0", "0", exp_date="2027-01-01")
        second = self.receive(self.ready("1"), "1", "0", "0", exp_date="2028-01-01")
        second = self.putaway(second)
        self.assertTrue(second["is_closed"])
        moved = InventoryLayerPosition.objects.get(location=self.location2, remaining_qty__gt=0)
        self.assertEqual(str(moved.layer.expiry_date), "2028-01-01")
        remaining = InventoryLayerPosition.objects.get(location=self.location, remaining_qty__gt=0)
        self.assertEqual(remaining.layer.source_id, str(first["batches"][0]["id"]))

    def test_pda_excludes_missing_wrong_owner_and_cyclic_sources(self):
        from .views import InboundTaskViewSet

        obj = self.ready("1")
        for i, source in enumerate(
            [
                {"source_model": "InboundOrder", "source_pk": "missing"},
                {"source_model": "WmsTask", "source_pk": "999999"},
                {"source_model": "InboundOrder", "source_pk": str(obj["id"]) + "junk"},
                {
                    "source_model": "InboundOrder",
                    "source_pk": str(obj["id"]),
                    "owner_id": self.other.pk,
                },
            ]
        ):
            values = dict(
                task_no=f"INVALID-{i}",
                task_type="RECEIVE",
                status="RELEASED",
                warehouse=self.warehouse,
                owner_id=self.owner.pk,
                source_app="inbound",
            )
            WmsTask.objects.create(**{**values, **source})
        cycle = WmsTask.objects.create(
            task_no="CYCLE",
            task_type="PUTAWAY",
            status="RELEASED",
            warehouse=self.warehouse,
            owner=self.owner,
            source_app="tasking",
            source_model="WmsTask",
        )
        WmsTask.objects.filter(pk=cycle.pk).update(source_pk=str(cycle.pk))
        request = APIRequestFactory().get("/")
        force_authenticate(request, self.op)
        result = InboundTaskViewSet.as_view({"get": "list"})(request)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual([r["id"] for r in result.data["results"]], [obj["batches"][0]["task_id"]])

    def test_legacy_no_order_putaway_remains_available_without_entering_ordered_receiving(self):
        from .constants import PDA_NO_ORDER_RECEIVE_SOURCE_APP, PDA_NO_ORDER_RECEIVE_SOURCE_MODEL
        from .views import InboundTaskViewSet

        receive = WmsTask.objects.create(
            task_no="NO-ORDER",
            task_type="RECEIVE",
            status="RELEASED",
            owner=self.owner,
            warehouse=self.warehouse,
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        )
        putaway = WmsTask.objects.create(
            task_no="NO-ORDER-PUTAWAY",
            task_type="PUTAWAY",
            status="RELEASED",
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="tasking",
            source_model="WmsTask",
            source_pk=str(receive.pk),
        )
        request = APIRequestFactory().get("/")
        force_authenticate(request, self.op)
        result = InboundTaskViewSet.as_view({"get": "list"})(request)
        self.assertEqual([r["id"] for r in result.data["results"]], [putaway.pk])
        self.assertIsNone(result.data["results"][0]["arrival"])

    def test_creating_a_task_does_not_expose_another_operators_assignment(self):
        from allapp.tasking.models import TaskAssignment

        from .views import InboundTaskViewSet

        obj = self.draft("1")
        for action, user in [
            ("submit", self.user),
            ("owner-approve", self.manager),
            ("warehouse-confirm", self.wh),
        ]:
            obj = self.act(obj, action, user)
        task_id = obj["batches"][0]["task_id"]
        other = self.actor(
            "other-receiver", "warehouse_operator", "claim_task_as_wh_operator", "view_wmstask"
        )
        WmsTask.objects.filter(pk=task_id).update(created_by=self.op)
        TaskAssignment.objects.create(task_id=task_id, assignee=other)
        request = APIRequestFactory().get("/")
        force_authenticate(request, self.op)
        response = InboundTaskViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])


class ArrivalConcurrencyTests(TransactionTestCase):
    setUpTestData = classmethod(inventory_fixtures.OwnerInventoryDetailTests.setUpTestData.__func__)
    grant = ArrivalTests.grant
    actor = ArrivalTests.actor
    call = ArrivalTests.call
    draft = ArrivalTests.draft
    act = ArrivalTests.act
    batch = ArrivalTests.batch

    def setUp(self):
        self.setUpTestData()
        ArrivalTests.setUp(self)

    def test_concurrent_confirmation_and_approval_do_not_duplicate_task_or_stock(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from django.db import connections

        obj = self.act(self.draft("2"), "submit")
        obj = self.act(obj, "owner-approve", self.manager)

        def race(action, payload, receipt_id=None, actor_id=None):
            barrier = Barrier(2)

            def work():
                try:
                    actor = get_user_model().objects.get(pk=actor_id or self.wh.pk)
                    barrier.wait(timeout=15)
                    return self.call(
                        "batch_action" if receipt_id else "arrival_action",
                        {"request_id": str(uuid4()), **payload},
                        actor,
                        obj["id"],
                        operation=action,
                        **({"receipt_id": receipt_id} if receipt_id else {}),
                    ).status_code
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(work) for _ in range(2)]
                return sorted(f.result(timeout=45) for f in futures)

        self.assertEqual(race("warehouse-confirm", {"version": obj["version"]}), [200, 409])
        self.assertEqual(WmsTask.objects.filter(task_type="RECEIVE").count(), 1)
        obj = self.call("progress", {}, self.wh, obj["id"], method="get").data
        receipt = obj["batches"][0]
        self.assertEqual(
            race("claim", {"version": receipt["version"]}, receipt["id"], self.op.pk), [200, 409]
        )
        obj = self.call("progress", {}, self.wh, obj["id"], method="get").data
        obj = self.batch(
            obj,
            "batch-edit",
            lines=[
                {
                    "order_line_id": obj["lines"][0]["id"],
                    "location_id": self.location.pk,
                    "qty_ok": "2",
                }
            ],
        )
        obj = self.batch(obj, "batch-submit")
        receipt = obj["batches"][0]
        self.assertEqual(
            race("batch-approve", {"version": receipt["version"]}, receipt["id"]), [200, 409]
        )
        self.assertEqual(InventoryTransaction.objects.filter(tx_type="RECEIVE").count(), 1)
        self.assertEqual(InventoryDetail.objects.get(onhand_qty__gt=0).onhand_qty, 2)
