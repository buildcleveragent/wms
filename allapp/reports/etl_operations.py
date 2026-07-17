from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from allapp.baseinfo.models import Customer, Owner, Supplier
from allapp.billing.enums import AccrualStatus
from allapp.billing.models import BillingAccrual
from allapp.core.choices import InvTxType
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product
from allapp.tasking.models import ReceiveLineExtra, WmsTask, WmsTaskLine

from .etl_utils import ensure_datedim, upsert_scd2
from .models import (
    CustomerDim,
    FactBilling,
    FactInboundLine,
    FactInventorySnapshotDaily,
    FactInventoryTxn,
    FactOutboundLine,
    OwnerDim,
    ProductDim,
    SupplierDim,
    WarehouseDim,
)


ZERO = Decimal("0")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _seconds(start: datetime | None, end: datetime | None) -> int | None:
    start = _aware(start)
    end = _aware(end)
    if not start or not end or end < start:
        return None
    return int((end - start).total_seconds())


@transaction.atomic
def sync_dimensions() -> dict[str, int]:
    counts = defaultdict(int)
    for owner in Owner.objects.all().iterator(chunk_size=1000):
        _, changed = upsert_scd2(
            OwnerDim,
            {"owner_id": owner.pk},
            {"code": owner.code, "name": owner.name},
        )
        counts["owners"] += int(changed)

    for warehouse in Warehouse.objects.all().iterator(chunk_size=1000):
        _, changed = upsert_scd2(
            WarehouseDim,
            {"warehouse_id": warehouse.pk},
            {
                "owner_id": None,
                "code": warehouse.code,
                "name": warehouse.name,
                "city": "",
            },
        )
        counts["warehouses"] += int(changed)

    for product in Product.objects.select_related("base_uom").all().iterator(chunk_size=1000):
        _, changed = upsert_scd2(
            ProductDim,
            {"product_id": product.pk},
            {
                "owner_id": product.owner_id,
                "sku_code": product.sku or product.code,
                "name": product.name,
                "category_code": str(product.category_id or ""),
                "uom": getattr(product.base_uom, "code", "") or "EA",
                "net_weight_kg": product.weight or ZERO,
                "volume_m3": product.volume or ZERO,
                "shelf_life_days": product.shelf_life_days,
            },
        )
        counts["products"] += int(changed)

    for customer in Customer.objects.all().iterator(chunk_size=1000):
        _, changed = upsert_scd2(
            CustomerDim,
            {"customer_id": customer.pk},
            {
                "owner_id": customer.owner_id,
                "code": customer.code,
                "name": customer.name,
                "level": str(customer.level or ""),
            },
        )
        counts["customers"] += int(changed)

    for supplier in Supplier.objects.all().iterator(chunk_size=1000):
        _, changed = upsert_scd2(
            SupplierDim,
            {"supplier_id": supplier.pk},
            {
                "owner_id": supplier.owner_id,
                "code": supplier.code,
                "name": supplier.name,
            },
        )
        counts["suppliers"] += int(changed)
    return dict(counts)


def _current(model, **lookup):
    return model.objects.filter(is_current=True, **lookup).first()


def _source_q(order) -> Q:
    return Q(source_pk=str(order.pk)) & (
        Q(source_model__iexact=order._meta.model_name)
        | Q(source_model__iexact=order.__class__.__name__)
    )


def _task_tree(order) -> list[WmsTask]:
    direct = list(WmsTask.objects.filter(_source_q(order)))
    tasks = {task.id: task for task in direct}
    frontier = set(tasks)
    while frontier:
        children = list(
            WmsTask.objects.filter(
                source_model__iexact="WmsTask",
                source_pk__in=[str(pk) for pk in frontier],
            )
        )
        frontier = {task.id for task in children if task.id not in tasks}
        tasks.update({task.id: task for task in children})
    return list(tasks.values())


def root_order_ids_for_tasks(task_ids, order_model) -> set[int]:
    """Resolve task descendants back to their inbound/outbound order roots."""

    pending = {int(pk) for pk in task_ids if pk}
    seen = set()
    roots = set()
    valid_names = {order_model._meta.model_name.lower(), order_model.__name__.lower()}
    while pending:
        batch = list(
            WmsTask.objects.filter(id__in=pending).values(
                "id", "source_model", "source_pk"
            )
        )
        pending = set()
        for task in batch:
            if task["id"] in seen:
                continue
            seen.add(task["id"])
            source_model = (task["source_model"] or "").lower()
            source_pk = str(task["source_pk"] or "")
            if source_model in valid_names and source_pk.isdigit():
                roots.add(int(source_pk))
            elif source_model == "wmstask" and source_pk.isdigit():
                pending.add(int(source_pk))
    return roots


def _latest(
    tasks,
    task_type,
    field="finished_at",
    *,
    completed_only=True,
    posted_only=False,
):
    """Return a completed operational milestone, never a cancellation time."""

    values = []
    for task in tasks:
        if task.task_type != task_type:
            continue
        if completed_only and task.status != WmsTask.Status.COMPLETED:
            continue
        if posted_only and task.posting_status != WmsTask.PostingStatus.POSTED:
            continue
        value = getattr(task, field, None)
        if value:
            values.append(value)
    return max(values) if values else None


def _earliest(tasks, task_type, *fields):
    values = []
    for task in tasks:
        if task.task_type != task_type:
            continue
        if task.status == WmsTask.Status.CANCELLED:
            continue
        for field in fields:
            value = getattr(task, field, None)
            if value:
                values.append(value)
                break
    return min(values) if values else None


def _source_line_names(model) -> set[str]:
    """Return the legacy source-model spellings accepted for a business line."""

    return {
        model._meta.model_name.lower(),
        model.__name__.lower(),
        model._meta.label_lower,
    }


def _is_direct_source_line(*, source_id, source_model, line_ids, names) -> bool:
    """Whether a task row carries a trustworthy order-line reference.

    Older work orders did not always populate ``src_model``/``src_id``.  Such
    rows are deliberately handled by the product-level fallback below instead
    of guessing that an unrelated primary key belongs to an order line.
    """

    try:
        matches_id = int(source_id) in line_ids
    except (TypeError, ValueError):
        return False
    return matches_id and (source_model or "").lower() in names


def _allocate_to_lines(lines, totals: dict[int, Decimal]) -> dict[int, Decimal]:
    result = {line.id: ZERO for line in lines}
    by_product = defaultdict(list)
    for line in lines:
        by_product[line.product_id].append(line)
    for product_id, product_lines in by_product.items():
        remaining = Decimal(totals.get(product_id, ZERO))
        for index, line in enumerate(product_lines):
            if remaining <= 0:
                break
            plan = Decimal(line.base_qty or 0)
            value = remaining if index == len(product_lines) - 1 else min(plan, remaining)
            result[line.id] = value
            remaining -= value
    return result


def _task_line_totals_by_order_line(
    tasks,
    task_type,
    quantity_field,
    order_lines,
    order_line_model,
    *,
    completed_only=False,
) -> dict[int, Decimal]:
    """Attribute task quantities to their real order lines whenever possible.

    Product-only allocation is retained only as a backwards-compatible
    fallback for legacy tasks with no source-line reference.  This avoids
    moving quantities between two same-SKU order lines in new data.
    """

    line_ids = {line.id for line in order_lines}
    direct = {line.id: ZERO for line in order_lines}
    direct_line_ids = set()
    fallback = defaultdict(lambda: ZERO)
    task_ids = [task.id for task in tasks if task.task_type == task_type]
    if not task_ids:
        return direct

    qs = WmsTaskLine.objects.filter(task_id__in=task_ids, product_id__isnull=False)
    if completed_only:
        qs = qs.filter(task__status=WmsTask.Status.COMPLETED)
    names = _source_line_names(order_line_model)
    for row in qs.values("src_id", "src_model", "product_id").annotate(
        qty=Sum(quantity_field)
    ):
        qty = Decimal(row["qty"] or 0)
        if _is_direct_source_line(
            source_id=row["src_id"],
            source_model=row["src_model"],
            line_ids=line_ids,
            names=names,
        ):
            source_id = int(row["src_id"])
            direct[source_id] += qty
            direct_line_ids.add(source_id)
        else:
            fallback[row["product_id"]] += qty

    fallback_lines = [line for line in order_lines if line.id not in direct_line_ids]
    for line_id, qty in _allocate_to_lines(fallback_lines, fallback).items():
        direct[line_id] += qty
    return direct


def _transaction_totals_by_order_line(tasks, tx_type, order_lines, order_line_model):
    """Map posted inventory facts through task lines back to order lines."""

    line_ids = {line.id for line in order_lines}
    direct = {line.id: ZERO for line in order_lines}
    direct_line_ids = set()
    fallback = defaultdict(lambda: ZERO)
    task_ids = [task.id for task in tasks]
    if not task_ids:
        return direct, None

    rows = list(
        InventoryTransaction.objects.filter(
            src_model__iexact="WmsTask",
            src_id__in=task_ids,
            tx_type=tx_type,
            posted_at__isnull=False,
        ).values("src_line_id", "product_id", "qty_delta", "posted_at")
    )
    source_lines = {
        row["id"]: (row["src_id"], row["src_model"])
        for row in WmsTaskLine.objects.filter(
            id__in={row["src_line_id"] for row in rows if row["src_line_id"]}
        ).values("id", "src_id", "src_model")
    }
    names = _source_line_names(order_line_model)
    latest = None
    for row in rows:
        source_id, source_model = source_lines.get(row["src_line_id"], (None, ""))
        qty = abs(Decimal(row["qty_delta"] or 0))
        if _is_direct_source_line(
            source_id=source_id,
            source_model=source_model,
            line_ids=line_ids,
            names=names,
        ):
            source_id = int(source_id)
            direct[source_id] += qty
            direct_line_ids.add(source_id)
        else:
            fallback[row["product_id"]] += qty
        if row["posted_at"] and (latest is None or row["posted_at"] > latest):
            latest = row["posted_at"]

    fallback_lines = [line for line in order_lines if line.id not in direct_line_ids]
    for line_id, qty in _allocate_to_lines(fallback_lines, fallback).items():
        direct[line_id] += qty
    return direct, latest


def _receive_exception_totals(tasks):
    rows = (
        ReceiveLineExtra.objects.filter(line__task_id__in=[task.id for task in tasks])
        .values("line__src_id")
        .annotate(reject=Sum("qty_reject"), damage=Sum("qty_damage"))
    )
    return {
        row["line__src_id"]: (
            Decimal(row["reject"] or 0),
            Decimal(row["damage"] or 0),
        )
        for row in rows
        if row["line__src_id"]
    }


@transaction.atomic
def sync_inbound_facts(queryset=None) -> int:
    orders = InboundOrder.objects.all() if queryset is None else queryset
    count = 0
    for order in orders.select_related("owner", "warehouse", "supplier").iterator(chunk_size=200):
        lines = list(order.lines.select_related("product").order_by("line_no", "id"))
        if not lines:
            continue
        owner_dim = _current(OwnerDim, owner_id=order.owner_id)
        warehouse_dim = _current(WarehouseDim, warehouse_id=order.warehouse_id)
        supplier_dim = _current(SupplierDim, supplier_id=order.supplier_id)
        if not owner_dim or not warehouse_dim:
            continue

        tasks = _task_tree(order)
        receive_tasks = [
            task
            for task in tasks
            if task.task_type == WmsTask.TaskType.RECEIVE
            and task.status == WmsTask.Status.COMPLETED
            and task.posting_status == WmsTask.PostingStatus.POSTED
        ]
        received_by_line, receive_at = _transaction_totals_by_order_line(
            receive_tasks,
            InvTxType.RECEIVE,
            lines,
            InboundOrderLine,
        )
        exceptions = _receive_exception_totals(receive_tasks)
        putaway_at = _latest(
            tasks,
            WmsTask.TaskType.PUTAWAY,
            posted_only=True,
        )
        receive_at = receive_at or _latest(
            receive_tasks,
            WmsTask.TaskType.RECEIVE,
            "posted_at",
            posted_only=True,
        )

        for line in lines:
            product_dim = _current(ProductDim, product_id=line.product_id)
            if not product_dim:
                continue
            reject_qty, damage_qty = exceptions.get(line.id, (ZERO, ZERO))
            FactInboundLine.objects.update_or_create(
                line_id=line.id,
                defaults={
                    "order_id": order.id,
                    "owner": owner_dim,
                    "warehouse": warehouse_dim,
                    "supplier": supplier_dim,
                    "product": product_dim,
                    "order_date": ensure_datedim(order.biz_date),
                    "receive_date": ensure_datedim(receive_at.date()) if receive_at else None,
                    "putaway_date": ensure_datedim(putaway_at.date()) if putaway_at else None,
                    "qty_plan": line.base_qty or ZERO,
                    "qty_received": received_by_line[line.id],
                    "qty_reject": reject_qty,
                    "qty_damage": damage_qty,
                    "sec_to_receive": _seconds(order.created_at, receive_at),
                    "sec_to_putaway": _seconds(receive_at, putaway_at),
                },
            )
            count += 1
    return count


@transaction.atomic
def sync_outbound_facts(queryset=None) -> int:
    orders = OutboundOrder.objects.all() if queryset is None else queryset
    count = 0
    for order in orders.select_related("owner", "warehouse", "customer").iterator(chunk_size=200):
        lines = list(order.lines.select_related("product").order_by("line_no", "id"))
        if not lines:
            continue
        owner_dim = _current(OwnerDim, owner_id=order.owner_id)
        warehouse_dim = _current(WarehouseDim, warehouse_id=order.warehouse_id)
        customer_dim = _current(CustomerDim, customer_id=order.customer_id) if order.customer_id else None
        if not owner_dim or not warehouse_dim:
            continue

        tasks = _task_tree(order)
        alloc_at = _earliest(tasks, WmsTask.TaskType.PICK, "released_at", "created_at")
        pick_at = _latest(tasks, WmsTask.TaskType.PICK)
        pack_at = _latest(tasks, WmsTask.TaskType.PACK)
        ship_at = _latest(tasks, WmsTask.TaskType.DISPATCH)
        alloc_by_line = _task_line_totals_by_order_line(
            tasks,
            WmsTask.TaskType.PICK,
            "qty_plan",
            lines,
            OutboundOrderLine,
        )
        picked_by_line = _task_line_totals_by_order_line(
            tasks,
            WmsTask.TaskType.PICK,
            "qty_done",
            lines,
            OutboundOrderLine,
            completed_only=True,
        )
        packed_by_line = _task_line_totals_by_order_line(
            tasks,
            WmsTask.TaskType.PACK,
            "qty_done",
            lines,
            OutboundOrderLine,
            completed_only=True,
        )
        shipped_by_line = _task_line_totals_by_order_line(
            tasks,
            WmsTask.TaskType.DISPATCH,
            "qty_done",
            lines,
            OutboundOrderLine,
            completed_only=True,
        )

        for line in lines:
            product_dim = _current(ProductDim, product_id=line.product_id)
            if not product_dim:
                continue
            plan = Decimal(line.base_qty or 0)
            shipped = shipped_by_line[line.id]
            in_full = bool(plan > 0 and shipped >= plan)
            on_time = bool(in_full and ship_at and order.etd and ship_at <= order.etd)
            FactOutboundLine.objects.update_or_create(
                line_id=line.id,
                defaults={
                    "order_id": order.id,
                    "owner": owner_dim,
                    "warehouse": warehouse_dim,
                    "customer": customer_dim,
                    "product": product_dim,
                    "order_date": ensure_datedim(order.biz_date),
                    "ship_date": ensure_datedim(ship_at.date()) if ship_at else None,
                    "qty_plan": plan,
                    "qty_alloc": alloc_by_line[line.id],
                    "qty_picked": picked_by_line[line.id],
                    "qty_packed": packed_by_line[line.id],
                    "qty_shipped": shipped,
                    "sec_alloc": _seconds(order.created_at, alloc_at),
                    "sec_pick": _seconds(alloc_at, pick_at),
                    "sec_pack": _seconds(pick_at, pack_at),
                    "sec_ship": _seconds(pack_at or pick_at, ship_at),
                    "in_full": in_full,
                    "on_time": on_time,
                },
            )
            count += 1
    return count


@transaction.atomic
def sync_inventory_transactions(queryset=None) -> int:
    transactions = InventoryTransaction.objects.all() if queryset is None else queryset
    count = 0
    for tx in transactions.select_related("owner", "warehouse", "product").iterator(chunk_size=1000):
        owner_dim = _current(OwnerDim, owner_id=tx.owner_id)
        warehouse_dim = _current(WarehouseDim, warehouse_id=tx.warehouse_id)
        product_dim = _current(ProductDim, product_id=tx.product_id)
        if not owner_dim or not warehouse_dim or not product_dim or not tx.posted_at:
            continue
        task = None
        if (tx.src_model or "").lower() == "wmstask":
            task = WmsTask.objects.filter(pk=tx.src_id).only("task_type", "source_pk").first()
        order_type = "OTHER"
        order_model = None
        if task:
            if task.task_type == WmsTask.TaskType.RECEIVE:
                order_type, order_model = "INBOUND", InboundOrder
            elif task.task_type in {
                WmsTask.TaskType.PICK,
                WmsTask.TaskType.LOAD,
                WmsTask.TaskType.DISPATCH,
            }:
                order_type, order_model = "OUTBOUND", OutboundOrder
            elif task.task_type in {
                WmsTask.TaskType.PUTAWAY,
                WmsTask.TaskType.RELOC,
                WmsTask.TaskType.REPLEN,
            }:
                order_type = "TRANSFER"
            elif task.task_type == WmsTask.TaskType.COUNT:
                order_type = "COUNT"
            elif task.task_type == WmsTask.TaskType.ADJUST:
                order_type = "ADJUST"
        elif tx.tx_type in {InvTxType.ADJ_GAIN, InvTxType.ADJ_LOSS}:
            order_type = "ADJUST"
        root_ids = root_order_ids_for_tasks([task.id], order_model) if order_model and task else set()
        order_id = next(iter(root_ids)) if len(root_ids) == 1 else None
        FactInventoryTxn.objects.update_or_create(
            txn_id=tx.id,
            defaults={
                "occurred_at": tx.posted_at,
                "owner": owner_dim,
                "warehouse": warehouse_dim,
                "location_id": tx.location_id,
                "product": product_dim,
                "lot_no": tx.batch_no or "",
                "reason": None,
                "order_type": order_type,
                "order_id": order_id,
                "qty_delta": tx.qty_delta,
                "amount_delta": ZERO,
            },
        )
        count += 1
    return count


@transaction.atomic
def sync_inventory_snapshot(snapshot_date: date) -> int:
    date_dim = ensure_datedim(snapshot_date)
    FactInventorySnapshotDaily.objects.filter(snapshot_date=date_dim).delete()
    rows = []
    for detail in InventoryDetail.objects.select_related(
        "owner", "warehouse", "product"
    ).all().iterator(chunk_size=1000):
        owner_dim = _current(OwnerDim, owner_id=detail.owner_id)
        warehouse_dim = _current(WarehouseDim, warehouse_id=detail.warehouse_id)
        product_dim = _current(ProductDim, product_id=detail.product_id)
        if not owner_dim or not warehouse_dim or not product_dim:
            continue
        rows.append(
            FactInventorySnapshotDaily(
                snapshot_date=date_dim,
                owner=owner_dim,
                warehouse=warehouse_dim,
                location_id=detail.location_id,
                product=product_dim,
                lot_no=detail.batch_no or "",
                qty_onhand=detail.onhand_qty or ZERO,
                qty_alloc=detail.allocated_qty or ZERO,
                qty_available=detail.available_qty or ZERO,
                qty_damage=detail.damaged_qty or ZERO,
                qty_expired=(
                    detail.onhand_qty
                    if detail.expiry_date and detail.expiry_date < snapshot_date
                    else ZERO
                ),
                amount_value=ZERO,
            )
        )
    FactInventorySnapshotDaily.objects.bulk_create(rows, batch_size=1000)
    return len(rows)


@transaction.atomic
def sync_billing_facts(queryset=None) -> int:
    accruals = BillingAccrual.objects.all() if queryset is None else queryset
    count = 0
    for accrual in accruals.iterator(chunk_size=1000):
        owner_dim = _current(OwnerDim, owner_id=accrual.owner_id)
        warehouse_dim = _current(WarehouseDim, warehouse_id=accrual.warehouse_id)
        if not owner_dim or not warehouse_dim:
            continue
        lookup = {
            "owner": owner_dim,
            "warehouse": warehouse_dim,
            "date": ensure_datedim(accrual.service_date),
            "fee_type": accrual.charge_type,
            "dedup_key": accrual.acc_fingerprint,
        }
        if accrual.status == AccrualStatus.VOID:
            FactBilling.objects.filter(**lookup).delete()
            continue
        FactBilling.objects.update_or_create(
            **lookup,
            defaults={
                "amount": accrual.amount or ZERO,
            },
        )
        count += 1
    return count


def prune_stale_facts() -> dict[str, int]:
    """Remove facts whose live source row no longer exists or is no longer posted."""

    live_inbound = InboundOrderLine.objects.filter(
        order__is_deleted=False
    ).values_list("id", flat=True)
    live_outbound = OutboundOrderLine.objects.filter(
        order__is_deleted=False
    ).values_list("id", flat=True)
    live_transactions = InventoryTransaction.objects.filter(
        posted_at__isnull=False
    ).values_list("id", flat=True)
    live_accrual_keys = BillingAccrual.objects.exclude(
        status=AccrualStatus.VOID
    ).values_list("acc_fingerprint", flat=True)
    return {
        "inbound": FactInboundLine.objects.exclude(line_id__in=live_inbound).delete()[0],
        "outbound": FactOutboundLine.objects.exclude(line_id__in=live_outbound).delete()[0],
        "inventory_transactions": FactInventoryTxn.objects.exclude(
            txn_id__in=live_transactions
        ).delete()[0],
        "billing": FactBilling.objects.exclude(dedup_key__in=live_accrual_keys).delete()[0],
    }


def _qty(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.001"))


def _actual_received_qty_for_orders(orders) -> Decimal:
    """Source-of-truth received quantity using only posted RECEIVE tasks.

    Putaway and relocation both produce a RECEIVE leg for a location move, so
    they are intentionally excluded from receipt reconciliation.
    """

    total = ZERO
    for order in orders.iterator(chunk_size=200):
        receive_task_ids = [
            task.id
            for task in _task_tree(order)
            if task.task_type == WmsTask.TaskType.RECEIVE
            and task.status == WmsTask.Status.COMPLETED
            and task.posting_status == WmsTask.PostingStatus.POSTED
        ]
        if not receive_task_ids:
            continue
        quantity = InventoryTransaction.objects.filter(
            tx_type=InvTxType.RECEIVE,
            src_model__iexact="WmsTask",
            src_id__in=receive_task_ids,
            posted_at__isnull=False,
        ).aggregate(qty=Sum("qty_delta"))["qty"]
        total += Decimal(quantity or 0)
    return _qty(total)


def _actual_shipped_qty_for_orders(orders) -> Decimal:
    """Source-of-truth shipment quantity from completed dispatch task lines."""

    total = ZERO
    for order in orders.iterator(chunk_size=200):
        dispatch_task_ids = [
            task.id
            for task in _task_tree(order)
            if task.task_type == WmsTask.TaskType.DISPATCH
            and task.status == WmsTask.Status.COMPLETED
        ]
        if not dispatch_task_ids:
            continue
        quantity = WmsTaskLine.objects.filter(task_id__in=dispatch_task_ids).aggregate(
            qty=Sum("qty_done")
        )["qty"]
        total += Decimal(quantity or 0)
    return _qty(total)


def source_reconciliation(*, dfrom: date | None = None, dto: date | None = None) -> dict:
    """Reconcile a complete mart or one explicit business-date window.

    A ranged full load is a supported recovery/backfill operation. Comparing
    that window against every source row would make a clean, empty mart fail by
    construction, so both sides must use the same range.
    """

    if bool(dfrom) != bool(dto):
        raise ValueError("dfrom and dto must be supplied together")
    inbound_lines = InboundOrderLine.objects.filter(order__is_deleted=False)
    outbound_lines = OutboundOrderLine.objects.filter(order__is_deleted=False)
    transactions = InventoryTransaction.objects.filter(posted_at__isnull=False)
    accruals = BillingAccrual.objects.exclude(status=AccrualStatus.VOID)
    inbound_facts = FactInboundLine.objects.all()
    outbound_facts = FactOutboundLine.objects.all()
    transaction_facts = FactInventoryTxn.objects.all()
    billing_facts = FactBilling.objects.all()
    if dfrom:
        inbound_lines = inbound_lines.filter(order__biz_date__range=(dfrom, dto))
        outbound_lines = outbound_lines.filter(order__biz_date__range=(dfrom, dto))
        transactions = transactions.filter(posted_at__date__range=(dfrom, dto))
        accruals = accruals.filter(service_date__range=(dfrom, dto))
        inbound_facts = inbound_facts.filter(order_date__date__range=(dfrom, dto))
        outbound_facts = outbound_facts.filter(order_date__date__range=(dfrom, dto))
        transaction_facts = transaction_facts.filter(occurred_at__date__range=(dfrom, dto))
        billing_facts = billing_facts.filter(date__date__range=(dfrom, dto))
    inbound_orders = InboundOrder.objects.filter(
        id__in=inbound_lines.values("order_id")
    )
    outbound_orders = OutboundOrder.objects.filter(
        id__in=outbound_lines.values("order_id")
    )
    source = {
        "inbound_lines": inbound_lines.count(),
        "inbound_plan_qty": _qty(inbound_lines.aggregate(qty=Sum("base_qty"))["qty"]),
        "inbound_received_qty": _actual_received_qty_for_orders(inbound_orders),
        "outbound_lines": outbound_lines.count(),
        "outbound_plan_qty": _qty(outbound_lines.aggregate(qty=Sum("base_qty"))["qty"]),
        "outbound_shipped_qty": _actual_shipped_qty_for_orders(outbound_orders),
        "inventory_transactions": transactions.count(),
        "inventory_qty_delta": _qty(transactions.aggregate(qty=Sum("qty_delta"))["qty"]),
        "billing_rows": accruals.count(),
        "billing_amount": Decimal(accruals.aggregate(amount=Sum("amount"))["amount"] or 0),
    }
    facts = {
        "inbound_lines": inbound_facts.count(),
        "inbound_plan_qty": _qty(inbound_facts.aggregate(qty=Sum("qty_plan"))["qty"]),
        "inbound_received_qty": _qty(
            inbound_facts.aggregate(qty=Sum("qty_received"))["qty"]
        ),
        "outbound_lines": outbound_facts.count(),
        "outbound_plan_qty": _qty(outbound_facts.aggregate(qty=Sum("qty_plan"))["qty"]),
        "inventory_transactions": transaction_facts.count(),
        "inventory_qty_delta": _qty(transaction_facts.aggregate(qty=Sum("qty_delta"))["qty"]),
        "billing_rows": billing_facts.count(),
        "billing_amount": Decimal(billing_facts.aggregate(amount=Sum("amount"))["amount"] or 0),
        "outbound_shipped_qty": _qty(
            outbound_facts.aggregate(qty=Sum("qty_shipped"))["qty"]
        ),
    }
    differences = {
        key: {"source": str(source[key]), "fact": str(facts[key])}
        for key in source
        if source[key] != facts[key]
    }
    return {
        "ok": not differences,
        "differences": differences,
        "source": {key: str(value) for key, value in source.items()},
        "facts": {key: str(value) for key, value in facts.items()},
    }


def require_reconciliation(reconciliation: dict) -> None:
    if not reconciliation.get("ok"):
        raise RuntimeError(
            "报表事实与业务源对账不一致: "
            + ", ".join(sorted(reconciliation.get("differences", {})))
        )
