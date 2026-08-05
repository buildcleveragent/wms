from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.choices import InvTxType, ZoneType
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Container, Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import (
    RelocationRequest,
    RelocationReservation,
    TaskAssignment,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.relocation import (
    RelocationIdempotencyConflict,
    approve_request,
    create_container_request,
    create_layer_request,
    record_relocation,
    void_task,
)
from allapp.tasking.services import _run_posting_handler


class RelocationFlowTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="RLC-OWN", name="移库测试货主")
        self.warehouse = Warehouse.objects.create(code="RLC-WH", name="移库测试仓")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse, code="RLCSW", name="移库子仓"
        )
        self.source = Location.objects.create(
            warehouse=self.warehouse,
            code="RLCSW-01-01-01",
            name="来源位",
            zone_type=ZoneType.STORAGE,
        )
        self.target = Location.objects.create(
            warehouse=self.warehouse,
            code="RLCSW-01-01-02",
            name="目标位",
            zone_type=ZoneType.PICK,
        )
        self.each = ProductUom.objects.create(code="RLC-EA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="RLC-SKU",
            sku="RLC-SKU",
            name="移库商品",
            base_uom=self.each,
            volume=Decimal("0.010000"),
            weight=Decimal("1.000"),
            price=Decimal("10.00"),
            batch_control=True,
            expiry_control=False,
        )
        self.user = get_user_model().objects.create_user(username="reloc-user", password="x")

    def detail(self, *, qty="5", container=None, product=None):
        return InventoryDetail.objects.create(
            owner=self.owner,
            product=product or self.product,
            warehouse=self.warehouse,
            location=self.source,
            container=container,
            zone_type=self.source.zone_type,
            batch_no="BATCH-1",
            onhand_qty=Decimal(qty),
            allocated_qty=0,
            locked_qty=0,
            damaged_qty=0,
        )

    def start_task(self, task):
        TaskAssignment.objects.create(
            task=task, assignee=self.user, accepted_at=timezone.now()
        )
        task.status = WmsTask.Status.IN_PROGRESS
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at", "updated_at"])
        task.lines.update(status=WmsTaskLine.Status.IN_PROGRESS)

    def test_layer_request_approval_locks_and_posting_moves_exact_layer(self):
        source_detail = self.detail(qty="5")
        request = create_layer_request(
            owner=self.owner,
            warehouse=self.warehouse,
            lines=[
                {
                    "inventory_detail_id": source_detail.pk,
                    "qty": "5",
                    "to_location_id": self.target.pk,
                    "to_container_id": None,
                }
            ],
            reason="整理库位",
            by_user=self.user,
        )
        task = approve_request(request.pk, by_user=self.user)
        source_detail.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(request.status, RelocationRequest.Status.APPROVED)
        self.assertEqual(task.status, WmsTask.Status.RELEASED)
        self.assertEqual(source_detail.locked_qty, Decimal("5.0000"))
        self.assertEqual(source_detail.available_qty, Decimal("0.0000"))

        self.start_task(task)
        line = task.lines.get()
        payload = {
            "task_id": task.pk,
            "line_id": line.pk,
            "request_id": "reloc-record-0001",
            "from_location_code": self.source.code,
            "to_location_code": self.target.code,
            "product_code": self.product.code,
            "qty": Decimal("5"),
            "by_user": self.user,
        }
        first = record_relocation(**payload)
        duplicate = record_relocation(**payload)
        self.assertTrue(first["posting_required"])
        self.assertTrue(duplicate["idempotent"])
        _run_posting_handler(task.pk, by_user=self.user, note="移库测试")

        source_detail.refresh_from_db()
        target_detail = InventoryDetail.objects.get(
            owner=self.owner,
            product=self.product,
            location=self.target,
            container__isnull=True,
            batch_no="BATCH-1",
        )
        task.refresh_from_db()
        self.assertEqual(source_detail.onhand_qty, Decimal("0.0000"))
        self.assertEqual(source_detail.locked_qty, Decimal("0.0000"))
        self.assertEqual(target_detail.onhand_qty, Decimal("5.0000"))
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        reservation = RelocationReservation.objects.get(task_line=line)
        self.assertEqual(reservation.status, RelocationReservation.Status.CONSUMED)
        txs = list(
            InventoryTransaction.objects.filter(src_id=task.pk, src_model="WmsTask")
            .order_by("id")
        )
        self.assertEqual([tx.tx_type for tx in txs], [InvTxType.ISSUE, InvTxType.RECEIVE])
        self.assertEqual(txs[0].pair_id, txs[1].pair_id)
        self.assertEqual({tx.memo for tx in txs}, {"RELOC"})

    def test_idempotency_key_reuse_with_different_payload_conflicts(self):
        detail = self.detail(qty="5")
        request = create_layer_request(
            owner=self.owner,
            warehouse=self.warehouse,
            lines=[{"inventory_detail_id": detail.pk, "qty": "5", "to_location_id": self.target.pk}],
            reason="幂等测试",
            by_user=self.user,
        )
        task = approve_request(request.pk, by_user=self.user)
        self.start_task(task)
        line = task.lines.get()
        base = {
            "task_id": task.pk,
            "line_id": line.pk,
            "request_id": "reloc-conflict-0001",
            "from_location_code": self.source.code,
            "to_location_code": self.target.code,
            "product_code": self.product.code,
            "qty": Decimal("2"),
            "by_user": self.user,
        }
        record_relocation(**base)
        with self.assertRaises(RelocationIdempotencyConflict):
            record_relocation(**{**base, "qty": Decimal("3")})

    def test_void_releases_reservation_and_preserves_ignored_scan(self):
        detail = self.detail(qty="5")
        request = create_layer_request(
            owner=self.owner,
            warehouse=self.warehouse,
            lines=[{"inventory_detail_id": detail.pk, "qty": "5", "to_location_id": self.target.pk}],
            reason="作废测试",
            by_user=self.user,
        )
        task = approve_request(request.pk, by_user=self.user)
        self.start_task(task)
        line = task.lines.get()
        record_relocation(
            task_id=task.pk,
            line_id=line.pk,
            request_id="reloc-void-0001",
            from_location_code=self.source.code,
            to_location_code=self.target.code,
            product_code=self.product.code,
            qty=Decimal("2"),
            by_user=self.user,
        )
        void_task(task.pk, by_user=self.user, note="来源盘点异常")
        detail.refresh_from_db()
        task.refresh_from_db()
        reservation = RelocationReservation.objects.get(task_line=line)
        self.assertEqual(detail.locked_qty, Decimal("0.0000"))
        self.assertEqual(detail.onhand_qty, Decimal("5.0000"))
        self.assertEqual(task.status, WmsTask.Status.CANCELLED)
        self.assertEqual(reservation.status, RelocationReservation.Status.RELEASED)
        self.assertEqual(task.scan_logs.get().status, "IGNORED")
        self.assertFalse(InventoryTransaction.objects.filter(src_id=task.pk).exists())

    def test_whole_container_moves_descendants_inventory_and_hierarchy(self):
        root = Container.objects.create(
            warehouse=self.warehouse,
            owner=self.owner,
            scope=Container.Scope.PRIVATE,
            container_no="RLC-ROOT",
            container_type="PALLET",
            location=self.source,
        )
        child = Container.objects.create(
            warehouse=self.warehouse,
            owner=self.owner,
            scope=Container.Scope.PRIVATE,
            container_no="RLC-CHILD",
            location=self.source,
            parent=root,
        )
        detail = self.detail(qty="5", container=child)
        request = create_container_request(
            owner=self.owner,
            warehouse=self.warehouse,
            source_container=root,
            to_location=self.target,
            reason="整托移动",
            by_user=self.user,
        )
        task = approve_request(request.pk, by_user=self.user)
        self.start_task(task)
        line = task.lines.get()
        result = record_relocation(
            task_id=task.pk,
            line_id=line.pk,
            request_id="reloc-container-0001",
            from_location_code=self.source.code,
            to_location_code=self.target.code,
            from_container_code=child.container_no,
            to_container_code=child.container_no,
            product_code=child.container_no,
            qty=Decimal("5"),
            by_user=self.user,
        )
        self.assertTrue(result["posting_required"])
        _run_posting_handler(task.pk, by_user=self.user, note="整容器测试")
        root.refresh_from_db()
        child.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual(root.location_id, self.target.pk)
        self.assertEqual(child.location_id, self.target.pk)
        self.assertEqual(child.parent_id, root.pk)
        self.assertEqual(detail.location_id, self.target.pk)
        self.assertEqual(detail.container_id, child.pk)

    def test_target_capacity_blocks_request(self):
        self.target.max_weight_kg = Decimal("1")
        self.target.save()
        detail = self.detail(qty="5")
        with self.assertRaisesMessage(ValidationError, "最大承重"):
            create_layer_request(
                owner=self.owner,
                warehouse=self.warehouse,
                lines=[{"inventory_detail_id": detail.pk, "qty": "5", "to_location_id": self.target.pk}],
                reason="超重",
                by_user=self.user,
            )
