from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List

from django.core.exceptions import ValidationError

from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryTransaction
from allapp.tasking.models import WmsTask, WmsTaskLine

from .models import PosSaleLine

QTY4 = Decimal("0.0001")


def _q4(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(QTY4, rounding=ROUND_HALF_UP)


def _normalized_model_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


@dataclass(frozen=True)
class ResolvedSaleIssueLayer:
    mode: str
    issue_tx_id: int
    task_line_id: int | None
    inventory_detail_id: int
    qty: Decimal


def resolve_sale_issue_layers(
    sale_lines: Iterable[PosSaleLine],
    *,
    for_update: bool = False,
) -> Dict[int, List[ResolvedSaleIssueLayer]]:
    """Resolve legacy and task-posted POS issues without guessing bigint semantics."""

    lines = list(sale_lines)
    if not lines:
        return {}
    line_by_id = {line.id: line for line in lines}
    outbound_line_to_sale_line = {
        line.outbound_order_line_id: line
        for line in lines
        if line.outbound_order_line_id
    }
    if len(outbound_line_to_sale_line) != len(lines):
        raise ValidationError("POS 销售明细缺少出库单行，无法解析原销售库存层")

    legacy_qs = InventoryTransaction.objects.filter(
        src_model="PosSaleLine",
        src_id__in=line_by_id,
        tx_type=InvTxType.ISSUE,
    ).order_by("id")
    if for_update:
        legacy_qs = legacy_qs.select_for_update()

    legacy_by_line = defaultdict(list)
    for tx in legacy_qs:
        sale_line = line_by_id.get(tx.src_id)
        if sale_line is None or not tx.src_line_id or tx.qty_delta >= 0:
            raise ValidationError("旧 POS 销售出库流水关系不完整")
        if (
            tx.owner_id != sale_line.owner_id
            or tx.product_id != sale_line.product_id
            or tx.warehouse_id != sale_line.sale.warehouse_id
        ):
            raise ValidationError("旧 POS 销售出库流水归属不一致")
        legacy_by_line[sale_line.id].append(
            ResolvedSaleIssueLayer(
                mode="legacy",
                issue_tx_id=tx.id,
                task_line_id=None,
                inventory_detail_id=int(tx.src_line_id),
                qty=_q4(-tx.qty_delta),
            )
        )

    order_ids = {str(line.outbound_order_line.order_id) for line in lines}
    candidate_task_ids = set(
        WmsTask.all_objects.filter(
            source_model__iexact="outboundorder",
            source_pk__in=order_ids,
        ).values_list("id", flat=True)
    )
    candidate_task_ids.update(
        WmsTaskLine.all_objects.filter(
            src_model__iexact="OutboundOrderLine",
            src_id__in=outbound_line_to_sale_line,
        ).values_list("task_id", flat=True)
    )
    task_qs = WmsTask.all_objects.filter(id__in=candidate_task_ids).order_by("id")
    if for_update:
        task_qs = task_qs.select_for_update()
    tasks = list(task_qs)
    tasks_by_id = {task.id: task for task in tasks}

    task_line_qs = WmsTaskLine.all_objects.filter(task_id__in=tasks_by_id).order_by(
        "id"
    )
    if for_update:
        task_line_qs = task_line_qs.select_for_update()
    task_lines = {line.id: line for line in task_line_qs}

    task_tx_qs = InventoryTransaction.objects.filter(
        src_model="WmsTask",
        src_id__in=tasks_by_id,
        tx_type=InvTxType.ISSUE,
    ).order_by("id")
    if for_update:
        task_tx_qs = task_tx_qs.select_for_update()

    task_by_line = defaultdict(list)
    for tx in task_tx_qs:
        task = tasks_by_id.get(tx.src_id)
        task_line = task_lines.get(tx.src_line_id)
        if task is None or task_line is None or task_line.task_id != tx.src_id:
            raise ValidationError("新 POS 销售出库流水与任务行关系不完整")
        if (
            task.is_deleted
            or task_line.is_deleted
            or (task.source_app or "").strip().lower() != "pos"
            or task.task_type != WmsTask.TaskType.PICK
        ):
            raise ValidationError("新 POS 销售出库任务来源应用或任务类型无效")
        if not _normalized_model_name(task.source_model).endswith("outboundorder"):
            raise ValidationError("新 POS 销售出库任务来源模型无效")
        if not _normalized_model_name(task_line.src_model).endswith(
            "outboundorderline"
        ):
            raise ValidationError("新 POS 销售出库任务行来源模型无效")

        sale_line = outbound_line_to_sale_line.get(task_line.src_id)
        if sale_line is None:
            raise ValidationError("新 POS 销售出库任务行不属于当前销售单")
        expected_order_id = sale_line.outbound_order_line.order_id
        if task.source_pk != str(expected_order_id):
            raise ValidationError("新 POS 销售出库任务与出库单关系不一致")

        meta = task_line.plan_meta or {}
        try:
            pos_sale_id = int(meta["pos_sale_id"])
            pos_sale_line_id = int(meta["pos_sale_line_id"])
            outbound_order_id = int(meta["outbound_order_id"])
            outbound_line_id = int(meta["outbound_order_line_id"])
            detail_id = int(meta["source_inventory_detail_id"])
            reserved_qty = _q4(meta["reserved_qty"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("新 POS 销售出库任务行缺少有效追溯信息") from exc
        qty = _q4(-tx.qty_delta)
        if (
            tx.qty_delta >= 0
            or detail_id <= 0
            or pos_sale_id != sale_line.sale_id
            or pos_sale_line_id != sale_line.id
            or outbound_order_id != expected_order_id
            or outbound_line_id != sale_line.outbound_order_line_id
            or task_line.product_id != sale_line.product_id
            or task.ref_no != sale_line.sale.sale_no
            or meta.get("sale_no") != sale_line.sale.sale_no
            or tx.src_no != sale_line.sale.sale_no
            or tx.owner_id != sale_line.owner_id
            or tx.product_id != sale_line.product_id
            or tx.warehouse_id != sale_line.sale.warehouse_id
            or reserved_qty != qty
            or _q4(task_line.qty_plan) != qty
            or _q4(task_line.qty_done) != qty
        ):
            raise ValidationError("新 POS 销售出库流水、任务行与销售明细不一致")

        task_by_line[sale_line.id].append(
            ResolvedSaleIssueLayer(
                mode="task",
                issue_tx_id=tx.id,
                task_line_id=task_line.id,
                inventory_detail_id=detail_id,
                qty=qty,
            )
        )

    resolved = {}
    for sale_line in lines:
        legacy = legacy_by_line.get(sale_line.id, [])
        task_posted = task_by_line.get(sale_line.id, [])
        if legacy and task_posted:
            raise ValidationError(
                f"销售明细 {sale_line.id} 同时存在新旧出库流水，拒绝回补"
            )
        layers = legacy or task_posted
        if not layers:
            raise ValidationError(f"销售明细 {sale_line.id} 缺少可追溯的出库流水")
        issued_qty = _q4(sum((layer.qty for layer in layers), Decimal("0")))
        if issued_qty != _q4(sale_line.qty):
            raise ValidationError(
                f"销售明细 {sale_line.id} 出库数量 {issued_qty} 与销售数量不一致"
            )
        resolved[sale_line.id] = layers
    return resolved
