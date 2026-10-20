"""Scoped customer-return workflow and atomic, idempotent inventory restoration."""

import hashlib
import json
import logging
from collections import defaultdict
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.inventory.fifo import create_receipt_layer
from allapp.inventory.locking import (
    lock_warehouses_for_inventory_write,
    run_inventory_write_with_retry,
)
from allapp.inventory.models import InventoryDetail, InventoryLayerMovement, InventoryTransaction
from allapp.inventory.services import _refresh_summaries
from allapp.locations.models import Location
from allapp.outbound.authz import strict_order_queryset
from allapp.outbound.models import OutboundOrder
from allapp.outbound.progress import LINE_NAMES, TASK_LINE_NAMES, _tasks_for_order
from allapp.pos.models import PosSaleLine, PosSaleOrder
from allapp.tasking.models import WmsTaskLine

from .customer_return_models import (
    CustomerReturn,
    CustomerReturnCommand,
    CustomerReturnEvent,
    CustomerReturnLine,
    CustomerReturnPhoto,
    CustomerReturnReceipt,
    CustomerReturnReceiptLine,
)

ZERO = Decimal(0)
EDITABLE = {"DRAFT", "REJECTED"}
RESERVED = {"OWNER_PENDING", "WHS_PENDING", "RECEIVING", "PARTIAL", "POSTING"}
PHOTO_REASONS = {"DAMAGE", "QUALITY", "WRONG"}
REASONS = {
    "DAMAGE": "破损",
    "QUALITY": "质量问题",
    "WRONG": "错货",
    "UNWANTED": "不再需要",
    "OTHER": "其他",
}
LABELS = {
    "retry-posting": "重试库存入账",
    "photo": "补充证据",
    "create": "保存草稿",
    "edit": "修改申请",
    "submit": "提交申请",
    "withdraw": "撤回申请",
    "cancel": "取消申请",
    "owner-approve": "货主审核通过",
    "owner-reject": "货主退回",
    "warehouse-confirm": "仓库确认",
    "warehouse-reject": "仓库退回",
    "claim": "领取验收",
    "receive": "验收登记",
    "post": "库存回补完成",
    "close": "关闭剩余数量",
    "note": "补充说明",
}


class ReturnConflict(APIException):
    status_code = 409
    default_detail = "单据或可退额度已变化，请刷新后重试。"


def scope_for(user):
    scope = AccessScope.for_user(user)
    if not scope.is_valid or scope.is_global:
        raise PermissionDenied("需要明确的货主或仓库角色范围。")
    if set(scope.roles) & {"owner_manager", "owner_salesperson"} and len(scope.owner_ids) != 1:
        raise PermissionDenied("需要单一货主范围。")
    return scope


def has_role(user, role):
    return role in scope_for(user).roles


def require(user, role, permission):
    roles = {role} if isinstance(role, str) else set(role)
    if not roles.intersection(scope_for(user).roles) or not user.has_perm(f"inbound.{permission}"):
        raise PermissionDenied("没有此退货操作权限。")


def queryset(user):
    scope = scope_for(user)
    qs = CustomerReturn.objects.select_related(
        "original_order__customer", "owner", "warehouse", "created_by", "assignee"
    ).prefetch_related("receipts")
    if set(scope.roles) & {"owner_manager", "owner_salesperson"}:
        if len(scope.owner_ids) != 1:
            return qs.none()
        return qs.filter(
            original_order__in=strict_order_queryset(OutboundOrder.objects.all(), user)
        )
    if "warehouse_manager" in scope.roles and user.has_perm("inbound.manage_customer_return"):
        return qs.filter(warehouse_id__in=scope.warehouse_ids)
    if (
        "warehouse_operator" in scope.roles
        and user.has_perm("inbound.receive_customer_return")
        and user.has_perm("tasking.claim_task_as_wh_operator")
    ):
        return qs.filter(warehouse_id__in=scope.warehouse_ids).filter(
            Q(assignee=user) | Q(assignee__isnull=True, status__in=["RECEIVING", "PARTIAL"])
        )
    raise PermissionDenied("没有客户退货访问权限。")


def original_for(user, pk):
    require(user, {"owner_manager", "owner_salesperson"}, "apply_customer_return")
    obj = strict_order_queryset(OutboundOrder.objects.all(), user).filter(pk=pk).first()
    if obj is None:
        raise NotFound()
    return obj


def line_source(task_line, lines, originals):
    current, seen = task_line, set()
    while current and current.pk not in seen:
        seen.add(current.pk)
        if current.status == "CANCELLED" or current.product_id != task_line.product_id:
            return None
        metadata = current.plan_meta or {}
        if not isinstance(metadata, dict):
            return None
        name = (current.src_model or "").lower()
        meta = metadata.get("outbound_order_line_id")
        if meta:
            try:
                meta_line = originals.get(int(meta))
            except (ValueError, TypeError):
                return None
            if not meta_line or meta_line.product_id != task_line.product_id:
                return None
            if name in LINE_NAMES and meta_line.pk != current.src_id:
                return None
        if name in LINE_NAMES:
            result = originals.get(current.src_id)
            return result if result and result.product_id == task_line.product_id else None
        meta = metadata.get("outbound_order_line_id")
        if not name and meta:
            try:
                result = originals.get(int(meta))
            except (TypeError, ValueError):
                return None
            return result if result and result.product_id == task_line.product_id else None
        if name in TASK_LINE_NAMES:
            current = lines.get(current.src_id)
        else:
            return None
    return None


def fulfilment(order):
    """Return line-specific facts, never infer an allocation between duplicate products."""
    originals = {r.pk: r for r in order.lines.select_related("product__base_uom", "base_uom")}
    amounts = {pk: ZERO for pk in originals}
    issues_by_line = defaultdict(list)
    invalid = set()
    reason = ""
    if order.outbound_type != "SALES":
        reason = "仅支持销售订单退货。"
    if (
        PosSaleOrder.objects.filter(outbound_order=order).exists()
        or PosSaleLine.objects.filter(outbound_order_line__order=order).exists()
    ):
        reason = "POS 销售请使用现有收银退货流程。"
    tasks = {
        pk: t
        for pk, t in _tasks_for_order(order).items()
        if t.status != "CANCELLED" and t.task_type != "REPLEN"
    }
    changed = True
    while changed:
        changed = False
        for task_id, task in list(tasks.items()):
            if (task.source_model or "").lower() in {"wmstask", "tasking.wmstask"}:
                try:
                    valid_parent = int(task.source_pk) in tasks
                except (TypeError, ValueError):
                    valid_parent = False
                if not valid_parent:
                    tasks.pop(task_id)
                    changed = True
    lines = {
        r.pk: r
        for r in WmsTaskLine.objects.filter(task_id__in=tasks)
        .exclude(status="CANCELLED")
        .select_related("dispatchlineextra", "product__base_uom")
    }
    by_product = defaultdict(list)
    for line in originals.values():
        by_product[line.product_id].append(line)
        if (line.base_uom_id or line.product.base_uom_id) != line.product.base_uom_id:
            invalid.add(line.pk)
    for line in lines.values():
        task = tasks[line.task_id]
        if task.task_type != "DISPATCH" or task.status != "COMPLETED":
            continue
        original = line_source(line, lines, originals)
        unit = getattr(getattr(line, "dispatchlineextra", None), "base_uom_id", None)
        if original is None or (unit and unit != original.product.base_uom_id):
            invalid.update(r.pk for r in by_product[line.product_id])
        else:
            amounts[original.pk] += line.qty_done
    # Actual ISSUEs also provide provenance for serials and returned cost layers.
    issue_q = Q(
        src_model__in=[
            "OutboundOrder",
            "outbound.OutboundOrder",
            "outboundorder",
            "outbound.outboundorder",
        ],
        src_id=order.pk,
    )
    issue_q |= Q(
        src_model__in=["WmsTask", "tasking.WmsTask", "wmstask", "tasking.wmstask"], src_id__in=tasks
    )
    issues = InventoryTransaction.objects.filter(
        issue_q,
        owner_id=order.owner_id,
        warehouse_id=order.warehouse_id,
        tx_type="ISSUE",
        qty_delta__lt=0,
        pair_id__isnull=True,
    )
    assisted = order.processing_mode == "WAREHOUSE_ASSISTED"
    if assisted:
        amounts = {pk: ZERO for pk in originals}
    for tx in issues:
        source_line = None
        if tx.src_line_id:
            if "task" in tx.src_model.lower():
                tl = lines.get(tx.src_line_id)
                source_line = line_source(tl, lines, originals) if tl else None
            else:
                source_line = originals.get(tx.src_line_id)
        elif len(by_product[tx.product_id]) == 1:
            source_line = by_product[tx.product_id][0]
        if (
            source_line is None
            or source_line.product_id != tx.product_id
            or tx.base_unit != source_line.product.base_uom.code
            or tx.posted_at is None
        ):
            if assisted:
                invalid.update(r.pk for r in by_product[tx.product_id])
            continue
        issues_by_line[source_line.pk].append(tx)
        if assisted:
            amounts[source_line.pk] -= tx.qty_delta
    # Historical customer returns with string-only links cannot safely share an allowance.
    from .models import InboundOrderReturnInfo

    if InboundOrderReturnInfo.objects.filter(
        order__owner_id=order.owner_id, orig_outbound_order_no=order.order_no
    ).exists():
        reason = "存在历史退货记录，关联及已退数量待核对。"
    return originals, amounts, invalid, reason, issues_by_line


def allowance(order, exclude=None):
    originals, amounts, invalid, reason, issues = fulfilment(order)
    used, reserved = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    previous = list(
        CustomerReturnLine.objects.filter(order__original_order=order)
        .select_related("order")
        .prefetch_related("receipts__receipt")
    )
    for line in previous:
        accepted = sum((r.qty_ok + r.qty_damage for r in line.receipts.all()), ZERO)
        processed = sum((r.qty_ok + r.qty_damage + r.qty_reject for r in line.receipts.all()), ZERO)
        used[line.original_line_id] += accepted
        if line.active and line.order_id != exclude and line.order.status in RESERVED:
            reserved[line.original_line_id] += max(line.qty - processed, ZERO)
    results = []
    for pk, line in originals.items():
        available = max(amounts[pk] - used[pk] - reserved[pk], ZERO)
        row_reason = reason or ("数据待核对，商品单位或履约来源不完整。" if pk in invalid else "")
        if not row_reason and amounts[pk] <= 0:
            row_reason = "尚无可核对的实际履约数量。"
        results.append(
            dict(
                original_line_id=pk,
                original_line_no=line.line_no,
                product_id=line.product_id,
                product_name=line.product.name,
                product_code=line.product.code,
                spec=line.product.spec or "",
                unit=line.product.base_uom.code,
                unit_label=line.product.base_uom.name,
                fulfilled_qty=str(amounts[pk]),
                returned_qty=str(used[pk]),
                pending_qty=str(reserved[pk]),
                available_qty=str(available),
                can_apply=not row_reason and available > 0,
                reason=row_reason,
            )
        )
    return results, issues


def fingerprint(action, data):
    return hashlib.sha256(
        json.dumps([action, data], sort_keys=True, default=str).encode()
    ).hexdigest()


def event(order, user, action, reason="", snapshot=None):
    CustomerReturnEvent.objects.create(
        order=order, actor=user, action=action, reason=reason, snapshot=snapshot or {}
    )
    record_audit_event(
        action=f"inbound.cust_return.{action}",
        module="inbound",
        user=user,
        obj=order,
        owner_id=order.owner_id,
        warehouse_id=order.warehouse_id,
        metadata={"reason": reason, "version": order.version},
    )


def check_version(order, data):
    if data.get("version") != order.version:
        raise ReturnConflict()


def operator(order, user):
    require(user, "warehouse_operator", "receive_customer_return")
    if not user.has_perm("tasking.claim_task_as_wh_operator") or order.assignee_id != user.pk:
        raise PermissionDenied("请先领取本单验收任务。")


def edit_lines(order, data):
    originals = {
        line.pk: line for line in order.original_order.lines.select_related("product__base_uom")
    }
    existing = {line.pk: line for line in order.lines.filter(active=True)}
    seen = set()
    for row in data["lines"]:
        original = originals.get(row["original_line_id"])
        if not original or original.pk in seen:
            raise ValidationError("原订单行无效或重复。")
        seen.add(original.pk)
        line = existing.pop(row.get("id"), None)
        if row.get("id") and (line is None or line.original_line_id != original.pk):
            raise ValidationError("退货行不属于此申请。")
        if line is None:
            line = CustomerReturnLine(
                order=order,
                original_line=original,
                product=original.product,
                product_name=original.product.name,
                product_code=original.product.code,
                spec=original.product.spec or "",
                unit=original.product.base_uom.code,
            )
        if original.product.serial_control and row["qty"] % 1:
            raise ValidationError("序列号商品的申请数量必须为整数。")
        line.qty = row["qty"]
        line.reason_code = row["reason_code"]
        line.reason = row["reason"]
        line.save()
    for line in existing.values():
        line.active = False
        line.save(update_fields=["active"])


def submit(order, user):
    if order.status not in EDITABLE:
        raise ReturnConflict()
    rows, _ = allowance(order.original_order, exclude=order.pk)
    limits = {r["original_line_id"]: r for r in rows}
    lines = list(order.lines.filter(active=True).prefetch_related("photos"))
    if not lines:
        raise ValidationError("至少填写一条退货商品。")
    for line in lines:
        limit = limits.get(line.original_line_id)
        if not limit or not limit["can_apply"] or line.qty > Decimal(limit["available_qty"]):
            raise ValidationError(
                f"{line.product_name}：{(limit or {}).get('reason') or '超过剩余可退数量'}"
            )
        if line.reason_code in PHOTO_REASONS and not any(
            p.purpose == "APPLICATION" for p in line.photos.all()
        ):
            raise ValidationError(f"{line.product_name}：此退货原因必须上传照片。")
    order.photos.filter(purpose="APPLICATION", line__active=True).update(sealed=True)
    order.status = "WHS_PENDING" if has_role(user, "owner_manager") else "OWNER_PENDING"


def update_status(order):
    if order.status == "CLOSED":
        return
    lines = list(order.lines.filter(active=True).prefetch_related("receipts"))
    total = sum((line.qty for line in lines), ZERO)
    processed = sum(
        (r.qty_ok + r.qty_damage + r.qty_reject for line in lines for r in line.receipts.all()),
        ZERO,
    )
    if processed >= total:
        order.status = (
            "POSTING" if order.receipts.filter(posted_at__isnull=True).exists() else "COMPLETED"
        )
    else:
        order.status = "PARTIAL" if processed else "RECEIVING"


def receive(order, user, data):
    operator(order, user)
    if order.status not in {"RECEIVING", "PARTIAL"}:
        raise ReturnConflict()
    lines = {
        line.pk: line
        for line in order.lines.filter(active=True)
        .select_related("product")
        .prefetch_related("receipts")
    }
    _, issue_map = allowance(order.original_order)
    receipt = CustomerReturnReceipt.objects.create(order=order, actor=user)
    pending = defaultdict(lambda: ZERO)
    for item in data["lines"]:
        line = lines.get(item["line_id"])
        if not line:
            raise ValidationError("验收商品不属于本单。")
        location = Location.objects.filter(
            pk=item["location_id"],
            warehouse_id=order.warehouse_id,
            is_disabled=False,
            is_frozen=False,
        ).first()
        if not location:
            raise ValidationError("请选择本仓库有效库位。")
        total = item["qty_ok"] + item["qty_damage"] + item["qty_reject"]
        pending[line.pk] += total
        previous = sum((r.qty_ok + r.qty_damage + r.qty_reject for r in line.receipts.all()), ZERO)
        if total <= 0 or previous + pending[line.pk] > line.qty:
            raise ValidationError("累计验收数量不能超过申请量，且本次数量必须大于零。")
        if line.product.batch_control and not item.get("batch_no"):
            raise ValidationError("批次商品必须录入批次。")
        if (
            item.get("expiry_date")
            and item.get("production_date")
            and item["expiry_date"] < item["production_date"]
        ):
            raise ValidationError("有效期不能早于生产日期。")
        serial = (item.get("serial_no") or "").strip().upper()
        if line.product.serial_control:
            if any(
                item[key] not in {ZERO, Decimal(1)}
                for key in ("qty_ok", "qty_damage", "qty_reject")
            ):
                raise ValidationError("序列号商品的合格、残次或拒收数量必须为整件。")
            if (
                total != 1
                or not serial
                or not any(
                    (t.serial_no or "").upper() == serial for t in issue_map[line.original_line_id]
                )
            ):
                raise ValidationError("序列号必须逐件验收且能对应原出库记录。")
            if (
                CustomerReturnReceiptLine.objects.filter(
                    line__order__owner_id=order.owner_id,
                    line__order__original_order_id=order.original_order_id,
                    line__product_id=line.product_id,
                    serial_no=serial,
                )
                .filter(Q(qty_ok__gt=0) | Q(qty_damage__gt=0))
                .exists()
            ):
                raise ValidationError("该序列号已有退回记录，请核对。")
        elif serial:
            raise ValidationError("非序列号商品不能填写序列号。")
        photo_ids = item.get("photo_ids", [])
        photos = list(
            CustomerReturnPhoto.objects.filter(
                pk__in=photo_ids,
                order=order,
                line=line,
                actor=user,
                purpose="RECEIPT",
                sealed=False,
                receipt_line__isnull=True,
            )
        )
        if len(photos) != len(photo_ids):
            raise ValidationError("照片无效或已被使用。")
        if item["qty_damage"] + item["qty_reject"] > 0 and (not photos or not item.get("reason")):
            raise ValidationError("残次或拒收必须填写原因并上传照片。")
        receipt_line = CustomerReturnReceiptLine.objects.create(
            receipt=receipt,
            line=line,
            location=location,
            batch_no=item.get("batch_no", ""),
            production_date=item.get("production_date"),
            expiry_date=item.get("expiry_date"),
            serial_no=serial,
            qty_ok=item["qty_ok"],
            qty_damage=item["qty_damage"],
            qty_reject=item["qty_reject"],
            reason=item.get("reason", ""),
        )
        CustomerReturnPhoto.objects.filter(pk__in=photo_ids).update(
            receipt_line=receipt_line, sealed=True
        )
    update_status(order)
    return receipt


def command(user, action, data, pk=None):
    try:
        return _command(user, action, data, pk)
    except IntegrityError:
        if CustomerReturnCommand.objects.filter(actor=user, request_id=data["request_id"]).exists():
            raise ReturnConflict("请求标识已被使用，请重新加载。") from None
        raise


def _command(user, action, data, pk=None):
    """All allowance-changing commands serialize on the original order."""
    initial = queryset(user).filter(pk=pk).first() if pk else None
    if pk and initial is None:
        raise NotFound()
    original = original_for(user, data["original_order_id"]) if not pk else initial.original_order
    digest = fingerprint(f"{action}:{pk}", data)
    with transaction.atomic():
        # Align with inventory posting lock order to avoid cross-workflow deadlocks.
        lock_warehouses_for_inventory_write(original.warehouse_id)
        OutboundOrder.objects.select_for_update().get(pk=original.pk)
        old = CustomerReturnCommand.objects.filter(
            actor=user, request_id=data["request_id"]
        ).first()
        if old:
            if old.fingerprint != digest:
                raise ReturnConflict("相同请求标识不能用于不同内容。")
            if not queryset(user).filter(pk=old.order_id).exists():
                raise NotFound()
            return old.order_id, old.receipt_id
        if action == "create":
            order = CustomerReturn.objects.create(
                number=f"CR-{uuid4().hex[:24].upper()}",
                original_order=original,
                owner_id=original.owner_id,
                warehouse_id=original.warehouse_id,
                created_by=user,
            )
            edit_lines(order, data)
        else:
            order = queryset(user).select_for_update().get(pk=pk)
            check_version(order, data)
        reason = data.get("reason", "")
        receipt = None
        if action in {"edit", "submit", "withdraw", "cancel"}:
            require(user, {"owner_manager", "owner_salesperson"}, "apply_customer_return")
        if action == "edit":
            if order.status not in EDITABLE:
                raise ReturnConflict()
            edit_lines(order, data)
        elif action == "submit":
            submit(order, user)
        elif action in {"withdraw", "cancel"}:
            if order.status not in EDITABLE | {"OWNER_PENDING", "WHS_PENDING"}:
                raise ReturnConflict("仓库确认后请联系仓库关闭剩余数量。")
            order.status = "DRAFT" if action == "withdraw" else "CANCELLED"
        elif action in {"owner-approve", "owner-reject"}:
            require(user, "owner_manager", "approve_customer_return")
            if order.status != "OWNER_PENDING":
                raise ReturnConflict()
            order.status = "WHS_PENDING" if action == "owner-approve" else "REJECTED"
        elif action in {"warehouse-confirm", "warehouse-reject", "close"}:
            require(user, "warehouse_manager", "manage_customer_return")
            if action == "close":
                if order.status not in {"RECEIVING", "PARTIAL"}:
                    raise ReturnConflict()
                order.status = "CLOSED"
            else:
                if order.status != "WHS_PENDING":
                    raise ReturnConflict()
                order.status = "RECEIVING" if action == "warehouse-confirm" else "REJECTED"
        elif action == "claim":
            require(user, "warehouse_operator", "receive_customer_return")
            if (
                not user.has_perm("tasking.claim_task_as_wh_operator")
                or order.status not in {"RECEIVING", "PARTIAL"}
                or order.assignee_id not in {None, user.pk}
            ):
                raise ReturnConflict()
            order.assignee = user
        elif action == "receive":
            receipt = receive(order, user, data)
        elif action == "retry-posting":
            operator(order, user)
            receipt = order.receipts.filter(pk=data["receipt_id"]).first()
            if receipt is None:
                raise NotFound()
        elif action == "note":
            if not any(
                user.has_perm(f"inbound.{permission}")
                for permission in [
                    "apply_customer_return",
                    "approve_customer_return",
                    "manage_customer_return",
                    "receive_customer_return",
                ]
            ):
                raise PermissionDenied()
            if not reason:
                raise ValidationError("请填写补充说明。")
        if action in {"owner-reject", "warehouse-reject", "close"} and not reason:
            raise ValidationError("请填写原因。")
        order.version += 1
        order.save()
        snapshot = {
            "status": order.status,
            "lines": [
                {
                    "id": line.pk,
                    "original_line_id": line.original_line_id,
                    "product_name": line.product_name,
                    "unit": line.unit,
                    "qty": str(line.qty),
                    "reason_code": line.reason_code,
                    "reason": line.reason,
                    "photo_ids": [p.pk for p in line.photos.all() if p.purpose == "APPLICATION"],
                }
                for line in order.lines.filter(active=True).prefetch_related("photos")
            ],
        }
        if receipt:
            snapshot["receipt_id"] = receipt.pk
        event(order, user, action, reason, snapshot)
        CustomerReturnCommand.objects.create(
            actor=user,
            request_id=data["request_id"],
            fingerprint=digest,
            order=order,
            receipt=receipt,
        )
        return order.pk, receipt.pk if receipt else None


def post_receipt(user, receipt_id):
    initial = CustomerReturnReceipt.objects.select_related("order").get(pk=receipt_id)
    if not queryset(user).filter(pk=initial.order_id).exists():
        raise NotFound()
    operator(initial.order, user)

    def operation():
        with transaction.atomic():
            lock_warehouses_for_inventory_write(initial.order.warehouse_id)
            OutboundOrder.objects.select_for_update().get(pk=initial.order.original_order_id)
            order = CustomerReturn.objects.select_for_update().get(pk=initial.order_id)
            operator(order, user)
            receipt = CustomerReturnReceipt.objects.select_for_update().get(pk=receipt_id)
            if receipt.posted_at:
                return
            _, _, _, _, issue_map = fulfilment(order.original_order)
            now = timezone.now()
            touched = set()
            for row in receipt.lines.select_related("line__product__base_uom", "location"):
                qty = row.qty_ok + row.qty_damage
                if not qty:
                    continue
                line = row.line
                if line.unit != line.product.base_uom.code:
                    raise ValidationError("商品单位已变化，请核对后再入账。")
                if (
                    row.serial_no
                    and InventoryDetail.objects.filter(
                        owner_id=order.owner_id,
                        product_id=line.product_id,
                        serial_no_norm=row.serial_no,
                        onhand_qty__gt=0,
                    ).exists()
                ):
                    raise ValidationError("序列号已在库，不能重复入账。")
                dimensions = dict(
                    owner_id=order.owner_id,
                    warehouse_id=order.warehouse_id,
                    product_id=line.product_id,
                    location_id=row.location_id,
                    container_id=None,
                    batch_no=row.batch_no,
                    production_date=row.production_date,
                    expiry_date=row.expiry_date,
                    serial_no=row.serial_no,
                )
                detail = (
                    InventoryDetail.objects.select_for_update()
                    .filter(**dimensions, is_active=True)
                    .first()
                )
                if detail is not None and detail.base_unit != line.unit:
                    raise ValidationError("现存库存单位与退货单位不一致，请先核对。")
                if detail is None and row.serial_no:
                    detail = (
                        InventoryDetail.objects.select_for_update()
                        .filter(
                            owner_id=order.owner_id,
                            warehouse_id=order.warehouse_id,
                            product_id=line.product_id,
                            serial_no_norm=row.serial_no,
                            is_active=True,
                            onhand_qty=0,
                        )
                        .first()
                    )
                    if detail is not None:
                        for field, value in dimensions.items():
                            setattr(detail, field, value)
                        detail.subwarehouse_id = row.location.subwarehouse_id
                if detail is None:
                    detail = InventoryDetail(
                        **dimensions, subwarehouse_id=row.location.subwarehouse_id
                    )
                detail.onhand_qty = (detail.onhand_qty or ZERO) + qty
                detail.damaged_qty = (detail.damaged_qty or ZERO) + row.qty_damage
                detail.save()
                tx = InventoryTransaction.objects.create(
                    **dimensions,
                    subwarehouse_id=detail.subwarehouse_id,
                    zone_type=detail.zone_type,
                    tx_type="RECEIVE",
                    qty_delta=qty,
                    src_model="inbound.CustomerReturnReceiptLine",
                    src_id=row.pk,
                    src_line_id=line.pk,
                    src_no=order.number,
                    memo="销售退货入库",
                    posted_at=now,
                    posting_batch=f"CRR-{receipt.pk}",
                )
                if settings.INVENTORY_FIFO_ENABLED:
                    candidates = [
                        i.pk
                        for i in issue_map[line.original_line_id]
                        if (i.batch_no or "") == row.batch_no
                        and (i.serial_no or "").upper() == row.serial_no
                        and i.expiry_date == row.expiry_date
                    ]
                    costs = set(
                        InventoryLayerMovement.objects.filter(
                            inventory_transaction_id__in=candidates
                        ).values_list("layer__unit_cost", "layer__cost_currency")
                    )
                    cost, currency = next(iter(costs)) if len(costs) == 1 else (None, "")
                    create_receipt_layer(
                        owner_id=order.owner_id,
                        warehouse_id=order.warehouse_id,
                        product_id=line.product_id,
                        location_id=row.location_id,
                        quantity=qty,
                        source_type="CUSTOMER_RETURN",
                        source_id=receipt.pk,
                        source_line_id=row.pk,
                        received_date=now.date(),
                        batch_no=row.batch_no,
                        serial_no=row.serial_no,
                        expiry_date=row.expiry_date,
                        unit_cost=cost,
                        cost_currency=currency,
                        inventory_transaction_id=tx.pk,
                        by_user=user,
                    )
                touched.add((order.owner_id, line.product_id))
            _refresh_summaries(touched)
            receipt.posted_at = now
            receipt.posting_error = False
            receipt.save(update_fields=["posted_at", "posting_error"])
            update_status(order)
            order.version += 1
            order.save()
            event(order, user, "post", snapshot={"receipt_id": receipt.pk})

    try:
        run_inventory_write_with_retry(operation, operation_name="customer_return.post")
    except Exception:  # Atomic posting rolled back; expose only a business failure.
        logging.getLogger(__name__).exception(
            "Customer return posting failed: receipt=%s", receipt_id
        )
        CustomerReturnReceipt.objects.filter(pk=receipt_id, posted_at__isnull=True).update(
            posting_error=True
        )
        return False
    return True
