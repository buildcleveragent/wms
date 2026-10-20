"""Atomic stock posting for classified arrival batches and their putaway tasks."""

import logging
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from allapp.core.models import DocSequence
from allapp.inventory.fifo import create_receipt_layer, move_fifo
from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.inventory.models import InventoryCostLayer, InventoryDetail, InventoryTransaction
from allapp.inventory.services import _lock_journal, _refresh_summaries
from allapp.locations.models import Location
from allapp.tasking.models import TaskAssignment, WmsTask, WmsTaskLine
from allapp.tasking.services import task_release, task_start

from .arrival_services import (
    ArrivalConflict,
    event,
    order_for,
    owns_task,
    require,
    update_completion,
)
from .models import InboundOrder, InboundReceipt, InboundReceiptLine

ZERO = Decimal(0)


def dimensions(order, row, location_id=None):
    return dict(
        owner_id=order.owner_id,
        warehouse_id=order.warehouse_id,
        product_id=row.product_id,
        location_id=location_id or row.location_id,
        container_id=None,
        batch_no=row.lot_no or "",
        production_date=row.mfg_date,
        expiry_date=row.exp_date,
        serial_no=row.serial_no,
    )


def stock_row(order, row, location):
    dims = dimensions(order, row, location.pk)
    detail = InventoryDetail.objects.select_for_update().filter(**dims, is_active=True).first()
    if detail is None and row.serial_no:
        detail = (
            InventoryDetail.objects.select_for_update()
            .filter(
                owner_id=order.owner_id,
                warehouse_id=order.warehouse_id,
                product_id=row.product_id,
                serial_no_norm=row.serial_no,
                onhand_qty=0,
                is_active=True,
            )
            .first()
        )
        if detail:
            for key, value in dims.items():
                setattr(detail, key, value)
    if detail is None:
        detail = InventoryDetail(**dims)
    if detail.pk and detail.base_unit != row.base_uom:
        raise ValidationError("库存单位与验收单位不一致，请核对。")
    detail.subwarehouse_id = location.subwarehouse_id
    detail.zone_type = location.zone_type
    return detail


def ledger(order, row, task, line_id, qty, location, *, pair=None):
    return InventoryTransaction.objects.create(
        **dimensions(order, row, location.pk),
        subwarehouse_id=location.subwarehouse_id,
        zone_type=location.zone_type,
        tx_type=("RECEIVE" if pair is None else "MOVE_IN" if qty > 0 else "MOVE_OUT"),
        qty_delta=qty,
        src_model="WmsTask",
        src_id=task.pk,
        src_line_id=line_id,
        src_no=task.task_no,
        memo="采购到货验收入库" if pair is None else "到货上架（保留良品／残次属性）",
        posted_at=timezone.now(),
        posting_batch=f"ARR-{task.pk}",
        pair_id=pair,
    )


def receive_stock(order, receipt, task, user):
    rows = list(
        receipt.lines.filter(is_deleted=False).select_related(
            "product__base_uom", "location", "order_line"
        )
    )
    accepted = [r for r in rows if r.qty_ok + r.qty_damage > 0]
    if not accepted:
        receipt.stage = "NO_STOCK"
        task.posting_status = "POSTED"
        event(order, user, "no-stock", receipt=receipt, reason="全部拒收，无需库存入账／无需上架")
        return
    putaway = WmsTask.objects.create(
        owner=order.owner,
        warehouse=order.warehouse,
        task_type="PUTAWAY",
        task_no=DocSequence.next_code(
            doc_type="SJ", owner=order.owner, warehouse=order.warehouse, biz_date=order.biz_date
        ),
        source_app="tasking",
        source_model="WmsTask",
        source_pk=str(task.pk),
        ref_no=order.order_no,
        created_by=user,
    )
    touched = set()
    for row in accepted:
        if (
            not row.order_line_id
            or row.order_line.order_id != order.pk
            or row.order_line.product_id != row.product_id
            or row.location.warehouse_id != order.warehouse_id
            or row.location.is_deleted
            or row.location.is_disabled
            or row.location.is_frozen
        ):
            raise ValidationError("验收来源或库位关联不一致，请核对。")
        if row.base_uom != row.product.base_uom.code or row.product.owner_id != order.owner_id:
            raise ValidationError("商品单位或货主已变化，请核对后再入账。")
        if (
            row.serial_no
            and InventoryDetail.objects.filter(
                owner_id=order.owner_id,
                product_id=row.product_id,
                serial_no_norm=row.serial_no,
                onhand_qty__gt=0,
                is_active=True,
            ).exists()
        ):
            raise ValidationError("序列号已在库，不能重复收货。")
        qty = row.qty_ok + row.qty_damage
        detail = stock_row(order, row, row.location)
        detail.onhand_qty = (detail.onhand_qty or ZERO) + qty
        detail.damaged_qty = (detail.damaged_qty or ZERO) + row.qty_damage
        detail.save()
        tx = ledger(order, row, task, row.task_line_id, qty, row.location)
        InboundReceiptLine.objects.filter(pk=row.pk).update(inventory_transaction=tx)
        if settings.INVENTORY_FIFO_ENABLED:
            create_receipt_layer(
                owner_id=order.owner_id,
                warehouse_id=order.warehouse_id,
                product_id=row.product_id,
                location_id=row.location_id,
                quantity=qty,
                source_type="INBOUND_BATCH",
                source_id=receipt.pk,
                source_line_id=row.pk,
                received_date=timezone.now().date(),
                batch_no=row.lot_no or "",
                serial_no=row.serial_no,
                expiry_date=row.exp_date,
                unit_cost=row.unit_price if row.unit_price > 0 else None,
                cost_currency="CNY" if row.unit_price > 0 else "",
                inventory_transaction_id=tx.pk,
                by_user=user,
            )
        for quality, amount in (("OK", row.qty_ok), ("DAMAGED", row.qty_damage)):
            if amount:
                WmsTaskLine.objects.create(
                    task=putaway,
                    product=row.product,
                    qty_plan=amount,
                    from_location=row.location,
                    src_model="inbound.InboundReceiptLine",
                    src_id=row.pk,
                    plan_meta={"quality": quality, "receipt_line_id": row.pk},
                )
        touched.add((order.owner_id, row.product_id))
    _refresh_summaries(touched)
    task_release(request=SimpleNamespace(user=user), task=putaway)
    receipt.putaway_task = putaway
    receipt.stage = "POSTED"
    receipt.posted_at = timezone.now()
    task.posting_status = "POSTED"
    event(order, user, "post", receipt=receipt)


def record_putaway(order, receipt, user, data):
    target = receipt.putaway_task
    if (
        not target
        or target.posting_status == "POSTED"
        or target.status not in {"RELEASED", "IN_PROGRESS"}
    ):
        raise ArrivalConflict("当前上架任务不能登记。")
    owns_task(user, target)
    if target.status == "RELEASED":
        task_start(request=SimpleNamespace(user=user), task=target, arrival=True)
        event(order, user, "start", receipt=receipt, snapshot={"task_id": target.pk})
    lines = {r.pk: r for r in target.lines.select_for_update().exclude(status="CANCELLED")}
    records = data.get("putaway_lines", [])
    if not records or len({r["id"] for r in records}) != len(records):
        raise ValidationError("请填写本次上架数量，行号不能重复。")
    locations = {
        r.pk: r
        for r in Location.objects.filter(
            pk__in=[r["location_id"] for r in records],
            warehouse_id=order.warehouse_id,
            is_disabled=False,
            is_frozen=False,
            is_deleted=False,
        )
    }
    for payload in records:
        row = lines.get(payload["id"])
        location = locations.get(payload["location_id"])
        qty = payload["qty"]
        if not row or not location or location.pk == row.from_location_id:
            raise ValidationError("上架行或目标库位无效，目标须不同于收货库位。")
        if qty <= 0 or row.qty_done + qty > row.qty_plan:
            raise ValidationError("本次上架数量须大于零且不超过剩余量。")
        if row.qty_done and row.to_location_id != location.pk:
            raise ValidationError("同一上架行不能更改已登记目标库位。")
        row.to_location = location
        row.qty_done += qty
        row.status = "COMPLETED" if row.qty_done == row.qty_plan else "IN_PROGRESS"
        if row.status == "COMPLETED":
            row.finished_at, row.finished_by = timezone.now(), user
        row.save()
    completed = all(r.qty_done == r.qty_plan for r in lines.values())
    target.status = "COMPLETED" if completed else "IN_PROGRESS"
    target.review_status = "PENDING" if completed else "NONE"
    target.finished_at = timezone.now() if completed else None
    target.save(update_fields=["status", "review_status", "finished_at"])
    event(
        order,
        user,
        "putaway-record",
        receipt=receipt,
        snapshot={
            "lines": [
                {"id": p["id"], "qty": str(p["qty"]), "location_id": p["location_id"]}
                for p in records
            ],
        },
    )


def move_stock(order, receipt, task, user):
    receipt_rows = {r.pk: r for r in receipt.lines.select_related("product__base_uom", "location")}
    planned = defaultdict(lambda: ZERO)
    lines = list(
        task.lines.exclude(status="CANCELLED").select_related("from_location", "to_location")
    )
    for line in lines:
        row = receipt_rows.get(line.src_id)
        quality = line.plan_meta.get("quality")
        if (
            not row
            or line.src_model != "inbound.InboundReceiptLine"
            or quality not in {"OK", "DAMAGED"}
            or line.product_id != row.product_id
            or line.from_location_id != row.location_id
        ):
            raise ValidationError("上架任务与验收来源不一致，请核对。")
        planned[(row.pk, quality)] += line.qty_plan
    for row in receipt_rows.values():
        if planned[(row.pk, "OK")] != row.qty_ok or planned[(row.pk, "DAMAGED")] != row.qty_damage:
            raise ValidationError("上架计划与已接收的良品、残次数量不一致。")
    touched = set()
    for line in task.lines.exclude(status="CANCELLED").select_related(
        "from_location", "to_location"
    ):
        row = receipt_rows.get(line.src_id)
        if (
            not row
            or line.qty_done != line.qty_plan
            or not line.to_location_id
            or line.qty_done <= 0
        ):
            raise ValidationError("上架记录不完整，请核对。")
        if (
            line.to_location.warehouse_id != order.warehouse_id
            or line.to_location.is_deleted
            or line.to_location.is_disabled
            or line.to_location.is_frozen
            or line.from_location.is_deleted
            or line.from_location.is_disabled
            or line.from_location.is_frozen
        ):
            raise ValidationError("目标库位不可用。")
        source = (
            InventoryDetail.objects.select_for_update()
            .filter(**dimensions(order, row), is_active=True)
            .first()
        )
        damage = line.plan_meta.get("quality") == "DAMAGED"
        qty = line.qty_done
        available = (source.damaged_qty if damage else source.available_qty) if source else ZERO
        if available < qty:
            raise ValidationError("原收货库位的良品或残次数量不足，请核对。")
        pair = uuid4()
        ledger(order, row, task, line.pk, -qty, line.from_location, pair=pair)
        tx = ledger(order, row, task, line.pk, qty, line.to_location, pair=pair)
        if row.serial_no:
            # One active serial row across all locations; relocate without changing its quality.
            source.location = line.to_location
            source.subwarehouse_id = line.to_location.subwarehouse_id
            source.zone_type = line.to_location.zone_type
            source.save()
        else:
            source.onhand_qty -= qty
            source.damaged_qty -= qty if damage else ZERO
            source.save()
            dest = stock_row(order, row, line.to_location)
            dest.onhand_qty = (dest.onhand_qty or ZERO) + qty
            dest.damaged_qty = (dest.damaged_qty or ZERO) + (qty if damage else ZERO)
            dest.save()
        if settings.INVENTORY_FIFO_ENABLED:
            move_fifo(
                owner_id=order.owner_id,
                warehouse_id=order.warehouse_id,
                product_id=row.product_id,
                from_location_id=line.from_location_id,
                to_location_id=line.to_location_id,
                quantity=qty,
                batch_no=row.lot_no or "",
                serial_no=row.serial_no,
                inventory_transaction_id=tx.pk,
                by_user=user,
                layer_ids=InventoryCostLayer.objects.filter(
                    source_type="INBOUND_BATCH",
                    source_id=str(receipt.pk),
                    source_line_id=str(row.pk),
                ).values("pk"),
            )
        touched.add((order.owner_id, row.product_id))
    _refresh_summaries(touched)
    task.posting_status = "POSTED"
    event(order, user, "putaway-post", receipt=receipt)


def post_batch(user, receipt_id, *, putaway=False):
    """Keep accepted evidence on failure; successful stock, costs and audit commit together."""
    require(user, {"warehouse_manager"}, "inbound.manage_arrival")
    initial = InboundReceipt.objects.select_related("order").get(pk=receipt_id)
    order_for(user, initial.order_id)
    task_id = initial.putaway_task_id if putaway else initial.task_id
    if not task_id:
        raise ArrivalConflict("尚无可入账任务。")
    try:
        with transaction.atomic():
            lock_warehouses_for_inventory_write(initial.warehouse_id)
            order = InboundOrder.objects.select_for_update().get(pk=initial.order_id)
            receipt = InboundReceipt.objects.select_for_update().get(pk=receipt_id)
            task = WmsTask.objects.select_for_update().get(pk=task_id)
            if task.posting_status == "POSTED" or (not putaway and receipt.stage == "NO_STOCK"):
                return True
            if (
                task.review_status != "APPROVED"
                or task.status != "COMPLETED"
                or (not putaway and receipt.stage != "APPROVED")
            ):
                raise ArrivalConflict("仅审核通过的完整批次可以入账。")
            if (receipt.owner_id, receipt.warehouse_id, task.owner_id, task.warehouse_id) != (
                order.owner_id,
                order.warehouse_id,
                order.owner_id,
                order.warehouse_id,
            ):
                raise ValidationError("任务与申请的货主或仓库不一致，请核对。")
            expected = (
                ("tasking", "WmsTask", str(receipt.task_id))
                if putaway
                else ("inbound", "InboundOrder", str(order.pk))
            )
            if (
                task.source_app,
                task.source_model,
                task.source_pk,
            ) != expected or task.task_type != ("PUTAWAY" if putaway else "RECEIVE"):
                raise ValidationError("任务来源关联无法核对。")
            journal = _lock_journal("WmsTask", task.pk, "POST")
            if journal.status == "POSTED":
                raise ArrivalConflict("入账凭据与任务状态不一致，请核对。")
            if putaway:
                move_stock(order, receipt, task, user)
            else:
                receive_stock(order, receipt, task, user)
            task.posted_at, task.posted_by = (
                (timezone.now(), user)
                if task.posting_status == "POSTED" and receipt.stage != "NO_STOCK"
                else (None, None)
            )
            task.save(update_fields=["posting_status", "posted_at", "posted_by"])
            TaskAssignment.objects.filter(task=task, finished_at__isnull=True).update(
                finished_at=timezone.now()
            )
            receipt.posting_error = False
            receipt.version += 1
            receipt.save()
            if receipt.stage == "NO_STOCK":
                journal.delete()
            else:
                journal.status, journal.message = "POSTED", "入账成功"
                journal.attempt_count += 1
                journal.save()
            order.version += 1
            order.save(update_fields=["version"])
            update_completion(order)
        return True
    except ArrivalConflict:
        raise
    except Exception:
        logging.getLogger(__name__).exception("Arrival posting failed for receipt %s", receipt_id)
        # Never return stack traces or internal posting payloads to the portal.
        with transaction.atomic():
            lock_warehouses_for_inventory_write(initial.warehouse_id)
            receipt = InboundReceipt.objects.select_for_update().get(pk=receipt_id)
            task = WmsTask.objects.select_for_update().get(pk=task_id)
            if task.posting_status != "POSTED" and receipt.stage != "NO_STOCK":
                InboundReceipt.objects.filter(pk=receipt_id).update(
                    posting_error=True, version=F("version") + 1
                )
                task.posting_status = "FAILED"
                task.save(update_fields=["posting_status"])
                journal = _lock_journal("WmsTask", task.pk, "POST")
                journal.status, journal.message = (
                    "FAILED",
                    "库存入账未完成，请仓库管理员核对后重试。",
                )
                journal.attempt_count += 1
                journal.save()
        return False
