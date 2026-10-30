"""Shared outbound documents built from validated source and posting facts."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q

from allapp.inventory.models import InventoryDetail, InventoryTransaction, PostingJournal
from allapp.inventory.services import _qty_for_type
from allapp.tasking.models import TaskScanLog

from .authz import strict_pick_queryset
from .physical_review import review_content


def document_queryset(qs, user):
    """Use the same task permission and scope for printing and completed document history."""
    if not user.is_authenticated or not (
        user.is_superuser
        or any(
            user.has_perm(permission)
            for permission in (
                "tasking.view_wmstask",
                "tasking.claim_task_as_wh_operator",
                "tasking.taskconfirm_as_wh_manager",
            )
        )
    ):
        return qs.none()
    return strict_pick_queryset(qs.filter(task_type="PICK"), user)


def completed_documents(qs):
    """Select successful tasks with the same journal requirement as delivery printing."""
    return qs.filter(
        status="COMPLETED",
        review_status="APPROVED",
        posting_status="POSTED",
        posted_at__isnull=False,
        pk__in=PostingJournal.objects.filter(
            src_model="WmsTask", tx_type="POST", status="POSTED"
        ).values("src_id"),
    )


def build_document(task, kind):
    """Reject incomplete evidence; never substitute order quantities for actual issues."""
    if task.status == "CANCELLED":
        raise ValidationError("已取消的任务不能打印。")
    if (
        kind == "delivery"
        and not completed_documents(type(task).objects.filter(pk=task.pk)).exists()
    ):
        raise ValidationError("任务尚未完成复核及库存过账，不能打印送货单。")
    content = review_content(task)
    if content["error"]:
        raise ValidationError(content["error"])
    order, task_lines = content["order"], content["lines"]
    origins = {
        row.pk: row
        for row in order.lines.select_related("product__base_uom", "base_uom", "aux_uom__uom")
    }
    if kind == "pick":
        lines = []
        for line in task_lines:
            meta = line.plan_meta or {}
            inventory_id = meta.get("inventory_detail_id") or meta.get("source_inventory_detail_id")
            detail = (
                InventoryDetail.objects.filter(pk=inventory_id).first() if inventory_id else None
            )
            transfer = getattr(line, "transfer_reservation", None)
            expected_owner = (
                transfer.transfer_line.source_product.owner_id if transfer else task.owner_id
            )
            expected_product = (
                transfer.transfer_line.source_product_id if transfer else line.product_id
            )
            if inventory_id and (
                detail is None
                or detail.owner_id != expected_owner
                or detail.product_id != expected_product
                or detail.warehouse_id != task.warehouse_id
                or detail.location_id != line.from_location_id
            ):
                raise ValidationError("拣货商品、货主、库位与分配库存不一致。")
            lines.append(
                {
                    "product": line.product,
                    "base_uom": origins[line.src_id].base_uom,
                    "location": line.from_location,
                    "lot_no": detail.batch_no if detail else "",
                    "base_qty": line.qty_plan,
                }
            )
        return order, lines

    journal = PostingJournal.objects.get(src_model="WmsTask", src_id=task.pk, tx_type="POST")
    by_task_line = {line.pk: line for line in task_lines}
    quantities, products = defaultdict(Decimal), defaultdict(Decimal)
    batches = set()
    scans = list(
        TaskScanLog.objects.filter(task=task)
        .filter(
            Q(posted_at__isnull=False)
            | Q(posting_journal__isnull=False)
            | Q(posting_batch__isnull=False)
        )
        .select_related("location")
    )
    if not scans:
        raise ValidationError("缺少已过账的有效扫描记录，不能生成送货单。")
    for scan in scans:
        line = by_task_line.get(scan.task_line_id)
        if (
            line is None
            or scan.status != "OK"
            or scan.review_status == "REJECTED"
            or scan.posting_journal_id != journal.pk
            or not scan.posted_at
            or not scan.posting_batch
            or scan.owner_id != task.owner_id
            or scan.warehouse_id != task.warehouse_id
            or (scan.product_id and scan.product_id != line.product_id)
            or (scan.location_id and scan.location.warehouse_id != task.warehouse_id)
        ):
            raise ValidationError("已过账扫描记录的来源或归属异常，不能生成送货单。")
        qty = -_qty_for_type("PICK", scan)
        quantities[line.src_id] += qty
        products[line.product_id] += qty
        batches.add(scan.posting_batch)
    ledger = defaultdict(Decimal)
    transactions = InventoryTransaction.objects.filter(
        src_model="WmsTask", src_id=task.pk, tx_type="ISSUE"
    )
    for tx in transactions:
        if (
            tx.owner_id != task.owner_id
            or tx.warehouse_id != task.warehouse_id
            or tx.posting_batch not in batches
        ):
            raise ValidationError("出库流水归属或过账批次异常。")
        ledger[tx.product_id] -= tx.qty_delta
    if not ledger or {k: v for k, v in products.items() if v} != dict(ledger):
        raise ValidationError("已过账扫描数量与库存出库流水不一致，不能生成送货单。")
    lines = []
    for origin in sorted(origins.values(), key=lambda row: (row.line_no, row.pk)):
        qty = quantities.get(origin.pk, Decimal(0))
        if qty < 0:
            raise ValidationError("订单行实际出库数量不能为负。")
        if qty == 0:
            continue
        if origin.is_priced and (origin.base_price is None or origin.base_price < 0):
            raise ValidationError("已计价订单行缺少有效单价。")
        origin.base_qty = qty
        origin.amount = (
            (qty * origin.base_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if origin.is_priced
            else None
        )
        if origin.aux_uom_id:
            if not origin.ratio or origin.ratio <= 0:
                raise ValidationError("商品包装换算比例无效。")
            origin.aux_qty = qty / origin.ratio
        lines.append(origin)
    if not lines:
        raise ValidationError("没有有效的实际出库数量。")
    return order, lines
