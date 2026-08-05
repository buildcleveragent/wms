import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.choices import InvTxType, ZoneType
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductPackage, ProductUom
from allapp.tasking.models import (
    ReplenishmentPolicy,
    ReplenishmentRequest,
    TaskAssignment,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.replenishment import (
    approve_request,
    evaluate_policy,
    record_replenishment,
)
from allapp.tasking.services import _run_posting_handler


class ReplenishmentFlowTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="RPL-OWN", name="补货测试货主")
        self.warehouse = Warehouse.objects.create(code="RPL-WH", name="补货测试仓")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse, code="RPLSW", name="补货测试子仓"
        )
        self.storage = Location.objects.create(
            warehouse=self.warehouse,
            code="RPLSW-01-01-01",
            name="存储位",
            zone_type=ZoneType.STORAGE,
        )
        self.pick = Location.objects.create(
            warehouse=self.warehouse,
            code="RPLSW-01-01-02",
            name="拣选位",
            zone_type=ZoneType.PICK,
        )
        self.other_storage = Location.objects.create(
            warehouse=self.warehouse,
            code="RPLSW-01-01-03",
            name="其他存储位",
            zone_type=ZoneType.STORAGE,
        )
        self.each = ProductUom.objects.create(code="RPL-EA", name="件", is_active=True)
        self.case = ProductUom.objects.create(code="RPL-CS", name="箱", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="RPL-SKU",
            sku="RPL-SKU",
            name="补货测试商品",
            base_uom=self.each,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
            batch_control=True,
            expiry_control=True,
        )
        ProductPackage.objects.create(
            product=self.product,
            uom=self.case,
            qty_in_base=10,
            barcode="RPL-CASE",
        )
        self.user = get_user_model().objects.create_user(
            username="replenishment-operator", password="x"
        )

    def _detail(self, *, qty, batch, expiry, location=None):
        return InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=location or self.storage,
            zone_type=(location or self.storage).zone_type,
            batch_no=batch,
            expiry_date=expiry,
            onhand_qty=Decimal(qty),
            allocated_qty=0,
            locked_qty=0,
            damaged_qty=0,
        )

    def _policy(self, **overrides):
        values = {
            "owner": self.owner,
            "warehouse": self.warehouse,
            "product": self.product,
            "target_location": self.pick,
            "min_qty": Decimal("10"),
            "target_qty": Decimal("25"),
            "replenish_uom": self.case,
            "source_zone_type": ZoneType.STORAGE,
            "auto_release": False,
            "demand_enabled": True,
        }
        values.update(overrides)
        return ReplenishmentPolicy.objects.create(**values)

    def test_policy_rejects_non_pick_target(self):
        with self.assertRaises(ValidationError):
            self._policy(target_location=self.other_storage)

    def test_minmax_rounds_to_package_splits_fefo_and_counts_in_transit(self):
        self._detail(qty="10", batch="LATE", expiry=datetime.date(2027, 6, 1))
        early = self._detail(qty="10", batch="EARLY", expiry=datetime.date(2027, 1, 1))
        self._detail(
            qty="5", batch="PICK", expiry=datetime.date(2027, 12, 1), location=self.pick
        )
        policy = self._policy()

        first = evaluate_policy(policy.pk, by_user=self.user)
        self.assertTrue(first["created"])
        task = first["task"]
        self.assertEqual(task.status, WmsTask.Status.DRAFT)
        self.assertEqual(
            sum((line.qty_plan for line in task.lines.all()), Decimal("0")),
            Decimal("20.000"),
        )
        self.assertEqual(task.lines.order_by("id").first().src_id, early.pk)

        second = evaluate_policy(policy.pk, by_user=self.user)
        self.assertFalse(second["created"])
        self.assertEqual(second["reason"], "ABOVE_MIN")
        self.assertEqual(
            WmsTask.objects.filter(task_type=WmsTask.TaskType.REPLEN).count(), 1
        )

    def test_manual_request_stays_pending_when_full_quantity_is_unavailable(self):
        self._detail(qty="9", batch="ONLY", expiry=datetime.date(2027, 1, 1))
        self._policy()
        request = ReplenishmentRequest.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            target_location=self.pick,
            requested_qty=Decimal("10"),
            reason="人工要货",
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, "当前最多可补"):
            approve_request(request.pk, by_user=self.user)

        request.refresh_from_db()
        self.assertEqual(request.status, ReplenishmentRequest.Status.PENDING)
        self.assertIsNone(request.generated_task_id)

    def test_record_is_idempotent_and_posts_paired_replenishment_move(self):
        source = self._detail(qty="10", batch="MOVE", expiry=datetime.date(2027, 1, 1))
        policy = self._policy(target_qty=Decimal("10"), auto_release=True)
        task = evaluate_policy(policy.pk, by_user=self.user)["task"]
        line = task.lines.get()
        TaskAssignment.objects.create(
            task=task, assignee=self.user, accepted_at=timezone.now()
        )
        task.status = WmsTask.Status.IN_PROGRESS
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at", "updated_at"])
        task.lines.update(status=WmsTaskLine.Status.IN_PROGRESS)

        payload = {
            "task_id": task.pk,
            "line_id": line.pk,
            "request_id": "replen-record-0001",
            "from_location_code": self.storage.code,
            "to_location_code": self.pick.code,
            "product_code": self.product.code,
            "qty": Decimal("10"),
            "by_user": self.user,
        }
        first = record_replenishment(**payload)
        duplicate = record_replenishment(**payload)
        self.assertFalse(first["idempotent"])
        self.assertTrue(first["posting_required"])
        self.assertTrue(duplicate["idempotent"])

        _run_posting_handler(task.pk, by_user=self.user, note="补货测试过账")

        source.refresh_from_db()
        target = InventoryDetail.objects.get(
            owner=self.owner,
            product=self.product,
            location=self.pick,
            batch_no="MOVE",
        )
        task.refresh_from_db()
        self.assertEqual(source.onhand_qty, Decimal("0.0000"))
        self.assertEqual(target.onhand_qty, Decimal("10.0000"))
        self.assertEqual(target.zone_type, ZoneType.PICK)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        transactions = list(
            InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk)
            .order_by("id")
            .values_list("tx_type", "pair_id")
        )
        self.assertEqual(
            {row[0] for row in transactions},
            {InvTxType.MOVE_OUT, InvTxType.MOVE_IN},
        )
        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0][1], transactions[1][1])
