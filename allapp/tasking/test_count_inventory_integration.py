"""Exercise actual stock-changing services while a count scope is frozen."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from allapp.baseinfo.models import Customer
from allapp.inventory import services as inventory_services
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Location
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.services import allocate_inventory
from allapp.pos.models import PosSale, PosShift
from allapp.pos.services import create_pos_sale
from allapp.products.models import Product
from allapp.tasking import counting, relocation
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.test_count_acceptance import case as count_case_fixture
from allapp.tasking.test_count_acceptance import detail, release

pytestmark = pytest.mark.django_db
case = count_case_fixture


def stock_posting(case, kind, location=None):
    location = location or case.location
    task = WmsTask.objects.create(
        owner=case.owner,
        warehouse=case.warehouse,
        task_no=f"COUNT-GUARD-{kind}",
        task_type=kind,
        status="COMPLETED",
        review_status="APPROVED",
        posting_status="PENDING",
    )
    line = WmsTaskLine.objects.create(
        task=task,
        product=case.product,
        from_location=location,
        to_location=location,
        qty_plan=1,
        qty_done=1,
        status="COMPLETED",
    )
    scan = TaskScanLog.objects.create(
        owner=case.owner,
        warehouse=case.warehouse,
        task=task,
        task_line=line,
        product=case.product,
        location=location,
        qty_base=Decimal("1"),
        qty_base_delta=Decimal("1"),
        fp=f"count-guard-{kind}",
        scan_snapshot_rev=0,
    )
    return task, scan


@pytest.mark.parametrize("kind", ["RECEIVE", "PICK"])
def test_frozen_count_blocks_real_posting_until_cancelled(case, kind):
    count = release(case)
    task, scan = stock_posting(case, kind)
    with pytest.raises(ValidationError, match="盘点冻结"):
        inventory_services.post_task(task=task, user=case.manager, scans=[scan])
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5
    assert not InventoryTransaction.objects.exists()
    counting.cancel_count_task(count.pk, by_user=case.manager)
    inventory_services.post_task(task=task, user=case.manager, scans=[scan])
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == (6 if kind == "RECEIVE" else 4)


@pytest.mark.parametrize("kind", ["RECEIVE", "PICK"])
def test_unfrozen_location_accepts_real_stock_writes(case, kind):
    other_location = Location.objects.create(
        warehouse=case.warehouse,
        subwarehouse=case.subwarehouse,
        code="COUNTSW-01-01-02",
        name="范围外库位",
    )
    other = detail(case, location=other_location, qty="5", batch="")
    release(case, scope="LOC", location_id=case.location.pk)
    task, scan = stock_posting(case, kind, other_location)
    inventory_services.post_task(task=task, user=case.manager, scans=[scan])
    other.refresh_from_db()
    case.detail.refresh_from_db()
    assert other.onhand_qty == (6 if kind == "RECEIVE" else 4)
    assert case.detail.onhand_qty == 5


def test_frozen_count_blocks_outbound_reservation_without_partial_allocation(case):
    count = release(case)
    customer = Customer.objects.create(
        owner=case.owner, code="CNT-CUST", name="测试客户", salesperson=case.manager
    )
    order = OutboundOrder.objects.create(
        owner=case.owner, warehouse=case.warehouse, customer=customer, order_no="CNT-ORDER"
    )
    OutboundOrderLine.objects.create(order=order, product=case.product, base_qty=1, base_price=1)
    with pytest.raises(ValidationError, match="盘点冻结"):
        allocate_inventory(order, by_user=case.manager, allow_backorder=False)
    case.detail.refresh_from_db()
    assert case.detail.allocated_qty == 0
    assert case.detail.onhand_qty == 5
    counting.cancel_count_task(count.pk, by_user=case.manager)
    allocate_inventory(order, by_user=case.manager, allow_backorder=False)
    case.detail.refresh_from_db()
    assert case.detail.allocated_qty == 1


def test_frozen_count_blocks_real_pos_sale_and_releases_after_cancel(case):
    case.manager.warehouse = case.warehouse
    case.manager.save(update_fields=["warehouse"])
    PosShift.objects.create(
        shift_no="COUNT-QA-SHIFT",
        warehouse=case.warehouse,
        cashier=case.manager,
        opened_by=case.manager,
        opened_at=timezone.now(),
    )
    count = release(case)
    values = {
        "user": case.manager,
        "items": [{"product_id": case.product.pk, "qty": "1", "price": "1"}],
        "payment": {"method": "CASH", "amount_received": "1"},
        "idempotency_key": "count-qa-pos",
    }
    with pytest.raises(ValidationError, match="盘点冻结"):
        create_pos_sale(**values)
    assert not PosSale.objects.exists()
    assert not InventoryTransaction.objects.exists()
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5 and case.detail.allocated_qty == 0
    counting.cancel_count_task(count.pk, by_user=case.manager)
    create_pos_sale(**values)
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 4


@pytest.mark.parametrize("frozen_side", ["source", "target"])
def test_frozen_count_blocks_relocation_approval_on_either_side(case, frozen_side):
    target = Location.objects.create(
        warehouse=case.warehouse,
        subwarehouse=case.subwarehouse,
        code="COUNTSW-01-01-02",
        name="移库目标",
    )
    if frozen_side == "target":
        detail(case, location=target)
    frozen_location = case.location if frozen_side == "source" else target
    count = release(case, scope="LOC", location_id=frozen_location.pk)
    request = relocation.create_layer_request(
        owner=case.owner,
        warehouse=case.warehouse,
        lines=[{"inventory_detail_id": case.detail.pk, "qty": "1", "to_location_id": target.pk}],
        reason="count acceptance",
        by_user=case.manager,
    )
    with pytest.raises(ValidationError, match="盘点冻结"):
        relocation.approve_request(request.pk, by_user=case.manager)
    case.detail.refresh_from_db()
    assert case.detail.locked_qty == 0
    counting.cancel_count_task(count.pk, by_user=case.manager)
    task = relocation.approve_request(request.pk, by_user=case.manager)
    assert task.status == "RELEASED"


def test_nonoverlapping_batch_is_not_frozen(case):
    detail(case, batch="COUNTED")
    release(case, scope="SKU", product_id=case.product.pk, batch_no="COUNTED")
    counting.assert_inventory_not_count_locked(
        owner_id=case.owner.pk,
        warehouse_id=case.warehouse.pk,
        location_id=case.location.pk,
        product_id=case.product.pk,
        batch_no="UNRELATED",
    )
    with pytest.raises(ValidationError, match="盘点冻结"):
        counting.assert_inventory_not_count_locked(
            owner_id=case.owner.pk,
            warehouse_id=case.warehouse.pk,
            location_id=case.location.pk,
            product_id=case.product.pk,
            batch_no="COUNTED",
        )


@pytest.mark.parametrize("operation", ["sale", "relocation"])
def test_other_product_can_sell_or_relocate_while_count_remains_frozen(case, operation):
    product = Product.objects.create(
        owner=case.owner, code="COUNT-OUTSIDE", name="范围外商品", base_uom=case.uom, price=1
    )
    outside = detail(case, product=product, qty="5", batch="")
    count = release(case, scope="SKU", product_id=case.product.pk)
    if operation == "sale":
        case.manager.warehouse = case.warehouse
        case.manager.save(update_fields=["warehouse"])
        PosShift.objects.create(
            shift_no="COUNT-OUTSIDE-SHIFT",
            warehouse=case.warehouse,
            cashier=case.manager,
            opened_by=case.manager,
            opened_at=timezone.now(),
        )
        create_pos_sale(
            user=case.manager,
            items=[{"product_id": product.pk, "qty": "1", "price": "1"}],
            payment={"method": "CASH", "amount_received": "1"},
            idempotency_key="count-outside-sale",
        )
        outside.refresh_from_db()
        assert outside.onhand_qty == 4
        assert InventoryTransaction.objects.filter(product=product).count() == 1
    else:
        target = Location.objects.create(
            warehouse=case.warehouse,
            subwarehouse=case.subwarehouse,
            code="COUNTSW-01-01-02",
            name="范围外移库目标",
        )
        request = relocation.create_layer_request(
            owner=case.owner,
            warehouse=case.warehouse,
            lines=[{"inventory_detail_id": outside.pk, "qty": "1", "to_location_id": target.pk}],
            reason="count outside-scope acceptance",
            by_user=case.manager,
        )
        task = relocation.approve_request(request.pk, by_user=case.manager)
        assert task.status == "RELEASED"
        outside.refresh_from_db()
        assert outside.locked_qty == 1
    case.detail.refresh_from_db()
    assert case.detail.onhand_qty == 5
    assert count.count_scope_locks.filter(released_at__isnull=True).exists()
