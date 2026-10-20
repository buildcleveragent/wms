"""Owner ASN workflow and independent, versioned arrival batches."""

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Supplier
from allapp.baseinfo.owner_warehouse_access import owner_can_use_warehouse
from allapp.core.models import DocSequence
from allapp.locations.models import Location
from allapp.products.models import Product
from allapp.tasking import services as task_services
from allapp.tasking.models import TaskAssignment, WmsTask, WmsTaskLine

from .arrival_models import ArrivalCommand, ArrivalEvent, ArrivalPhoto
from .models import InboundOrder, InboundOrderLine, InboundReceipt, InboundReceiptLine

ZERO = Decimal(0)
CONFIRMED = {"APPROVED", "POSTED", "NO_STOCK"}
ACTIVE = CONFIRMED | {"PENDING"}
LABELS = {
    "create": "创建入库申请",
    "edit": "修改入库申请",
    "submit": "提交申请",
    "withdraw": "撤回申请",
    "owner-approve": "货主审核通过",
    "owner-reject": "货主退回",
    "warehouse-confirm": "仓库确认并发布",
    "warehouse-reject": "仓库退回",
    "close": "结束收货",
    "batch-create": "建立到货批次",
    "batch-edit": "保存验收草稿",
    "batch-submit": "提交本批验收",
    "batch-approve": "验收审核通过",
    "batch-reject": "验收退回",
    "post": "本批入账完成",
    "no-stock": "本批无需库存入账",
    "putaway-record": "登记上架",
    "putaway-approve": "上架审核通过",
    "putaway-post": "上架入账完成",
    "putaway-reject": "上架退回",
    "retry-posting": "重试库存入账",
    "claim": "领取任务",
    "start": "开始作业",
    "photo": "追加破损照片",
    "note": "补充说明",
}


class ArrivalConflict(APIException):
    status_code = 409
    default_detail = "单据或任务已变化，请刷新后重试。"


def scope_for(user):
    scope = AccessScope.for_user(user)
    if not scope.is_valid or scope.is_global:
        raise PermissionDenied("需要有效的货主或仓库角色范围。")
    if scope.roles & {"owner_salesperson", "owner_manager"} and len(scope.owner_ids) != 1:
        raise PermissionDenied("需要单一货主范围。")
    return scope


def require(user, roles, permission):
    if not set(roles).intersection(scope_for(user).roles) or not user.has_perm(permission):
        raise PermissionDenied("没有执行此入库操作的权限。")


def orders_for(user):
    scope = scope_for(user)
    qs = InboundOrder.objects.filter(is_deleted=False).select_related(
        "owner", "warehouse", "supplier", "created_by"
    )
    if scope.roles & {"owner_manager", "owner_salesperson"}:
        if not any(
            user.has_perm(p)
            for p in (
                "inbound.view_inboundorder",
                "inbound.submit_as_owner_buyers",
                "inbound.approve_as_owner_manager",
            )
        ):
            raise PermissionDenied("没有入库申请查看权限。")
        qs = qs.filter(owner_id=scope.single_owner_id)
        if "owner_salesperson" in scope.roles:
            qs = qs.filter(created_by=user)
    elif "warehouse_manager" in scope.roles:
        require(user, {"warehouse_manager"}, "inbound.manage_arrival")
        qs = qs.filter(warehouse_id__in=scope.warehouse_ids)
    elif "warehouse_operator" in scope.roles:
        require(user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
        qs = (
            qs.filter(warehouse_id__in=scope.warehouse_ids, approval_status="WHS_APPROVED")
            .filter(
                Q(receipts__task__assignments__assignee=user)
                | Q(receipts__putaway_task__assignments__assignee=user)
                | Q(receipts__task__status="RELEASED", receipts__task__assignments__isnull=True)
                | Q(
                    receipts__putaway_task__status="RELEASED",
                    receipts__putaway_task__assignments__isnull=True,
                )
            )
            .distinct()
        )
    else:
        raise PermissionDenied("没有入库访问权限。")
    return qs


def order_for(user, pk, *, lock=False):
    qs = orders_for(user)
    if lock:
        qs = qs.select_related(None).select_for_update()
    obj = qs.filter(pk=pk).first()
    if obj is None:
        raise NotFound()
    return obj


def receipt_for(user, order, pk, *, lock=False):
    qs = InboundReceipt.objects.filter(
        order=order, owner_id=order.owner_id, warehouse_id=order.warehouse_id, is_deleted=False
    ).exclude(stage="LEGACY")
    if lock:
        qs = qs.select_for_update()
    receipt = qs.select_related("task", "putaway_task").filter(pk=pk).first()
    if not receipt:
        raise NotFound()
    if "warehouse_operator" in scope_for(user).roles:
        task = receipt.task
        owned = TaskAssignment.objects.filter(task=task, assignee=user).exists()
        putaway_owned = (
            receipt.putaway_task_id
            and TaskAssignment.objects.filter(
                task_id=receipt.putaway_task_id, assignee=user
            ).exists()
        )
        available = (
            task.status == "RELEASED"
            and not TaskAssignment.objects.filter(task=task, finished_at__isnull=True).exists()
        )
        putaway_available = (
            receipt.putaway_task_id
            and receipt.putaway_task.status == "RELEASED"
            and not TaskAssignment.objects.filter(
                task_id=receipt.putaway_task_id, finished_at__isnull=True
            ).exists()
        )
        if not (
            owned
            or putaway_owned
            or available
            or putaway_available
            or receipt.made_by_id == user.pk
        ):
            raise NotFound()
    return receipt


def check_version(obj, version):
    if version != obj.version:
        raise ArrivalConflict()


def event(order, user, action, *, receipt=None, reason="", snapshot=None):
    ArrivalEvent.objects.create(
        order=order,
        receipt=receipt,
        actor=user,
        action=action,
        reason=reason,
        snapshot={
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
            "receipt_stage": receipt.stage if receipt else None,
            **(snapshot or {}),
        },
    )
    record_audit_event(
        action=f"inbound.arrival.{action}",
        module="inbound",
        user=user,
        obj=order,
        metadata={
            "receipt_id": receipt.pk if receipt else None,
            "reason": reason,
            **(snapshot or {}),
        },
    )


def command(user, data, action, target, operation):
    """Serialize each request identity without locking the actor across inventory operations."""
    key = str(data.get("request_id") or "")
    if not 8 <= len(key) <= 64:
        raise ValidationError({"request_id": "需要 8 至 64 位请求编号。"})
    fp = hashlib.sha256(
        json.dumps([action, target, data], sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()
    with transaction.atomic():
        entry, _ = ArrivalCommand.objects.get_or_create(
            actor=user, request_id=key, defaults={"fingerprint": fp, "result": {}}
        )
        entry = ArrivalCommand.objects.select_for_update().get(pk=entry.pk)
        if entry.fingerprint != fp:
            raise ArrivalConflict("同一请求编号不能用于不同操作或内容。")
        if entry.result:
            return entry.result
        result = operation()
        entry.result = result
        entry.save(update_fields=["result"])
        return result


def bump(obj):
    obj.version += 1
    obj.save(update_fields=["version", "updated_at"])


def validate_draft(user, data):
    scope = scope_for(user)
    owner_id = scope.single_owner_id
    if not owner_can_use_warehouse(owner_id, data["warehouse_id"]):
        raise ValidationError({"warehouse_id": "仓库未授权给当前货主或已停用。"})
    supplier = Supplier.objects.filter(
        pk=data["supplier_id"], owner_id=owner_id, is_active=True, is_deleted=False
    ).first()
    if not supplier:
        raise ValidationError({"supplier_id": "供应商不存在或不属于当前货主。"})
    products = {
        p.pk: p
        for p in Product.objects.filter(
            pk__in={r["product_id"] for r in data["lines"]},
            owner_id=owner_id,
            is_active=True,
            is_deleted=False,
        ).select_related("base_uom")
    }
    if any(row["product_id"] not in products for row in data["lines"]):
        raise ValidationError("商品不存在、已停用或不属于当前货主。")
    return supplier, products


ORDER_FIELDS = (
    "eta",
    "delivery_contact",
    "delivery_phone",
    "vehicle_no",
    "delivery_note",
    "delivery_method",
    "src_bill_no",
    "biz_date",
)


def save_order(user, data, pk=None):
    require(
        user,
        {"owner_salesperson"},
        "inbound.change_inboundorder" if pk else "inbound.add_inboundorder",
    )

    if pk:
        order_for(user, pk)

    def operation():
        supplier, products = validate_draft(user, data)
        if pk:
            order = order_for(user, pk, lock=True)
            check_version(order, data.get("version"))
            if (
                order.receipt_mode != "BATCH"
                or order.submit_status != "DRAFT"
                or order.is_closed
                or order.approval_status not in {"NOT_READY", "OWNER_REJECTED", "WHS_REJECTED"}
            ):
                raise ArrivalConflict("仅草稿或退回申请可以修改。")
        else:
            order = InboundOrder(
                owner_id=scope_for(user).single_owner_id, created_by=user, receipt_mode="BATCH"
            )
        order.supplier = supplier
        order.warehouse_id = data["warehouse_id"]
        for field in ORDER_FIELDS:
            if field in data:
                setattr(order, field, data[field])
        order.save()
        existing = {row.pk: row for row in order.lines.filter(is_deleted=False)}
        used = set()
        for payload in data["lines"]:
            row_id = payload.get("id")
            if row_id and (row_id not in existing or row_id in used):
                raise ValidationError("订单行关联无效或重复。")
            row = existing[row_id] if row_id else InboundOrderLine(order=order)
            if row_id:
                used.add(row_id)
            row.product = products[payload["product_id"]]
            row.base_uom = row.product.base_uom.code
            row.base_qty = payload["base_qty"]
            row.base_price = payload.get("base_price", ZERO)
            row.note = payload.get("note", "")
            row.save()
        order.lines.filter(pk__in=set(existing) - used).update(is_deleted=True)
        if pk:
            bump(order)
        event(
            order,
            user,
            "edit" if pk else "create",
            snapshot={
                "lines": [
                    {"id": r.pk, "product_id": r.product_id, "qty": str(r.base_qty)}
                    for r in order.lines.filter(is_deleted=False)
                ]
            },
        )
        return {"order_id": order.pk}

    return command(user, data, "edit" if pk else "create", pk, operation)


def order_quantities(order, receipts=None):
    receipts = list(
        receipts
        if receipts is not None
        else order.receipts.exclude(stage="LEGACY")
        .filter(is_deleted=False)
        .prefetch_related("lines")
    )
    totals = defaultdict(lambda: defaultdict(lambda: ZERO))
    for receipt in receipts:
        if receipt.stage not in ACTIVE:
            continue
        for row in receipt.lines.all():
            if row.is_deleted or not row.order_line_id:
                continue
            values = totals[row.order_line_id]
            total = row.qty_ok + row.qty_damage + row.qty_reject
            if receipt.stage == "PENDING":
                values["pending"] += total
            else:
                values["ok"] += row.qty_ok
                values["damage"] += row.qty_damage
                values["reject"] += row.qty_reject
                if receipt.stage == "POSTED":
                    values["posted"] += row.qty_ok + row.qty_damage
    return totals


def new_batch(order, user, *, existing_task=None):
    if order.receiving_closed_at or order.is_closed:
        raise ArrivalConflict("收货已结束。")
    if order.receipts.filter(stage__in=["DRAFT", "REJECTED"]).exists():
        raise ArrivalConflict("已有未提交的到货批次，请先处理。")
    task = existing_task or WmsTask.objects.create(
        owner=order.owner,
        warehouse=order.warehouse,
        task_no=DocSequence.next_code(
            doc_type="SH", owner=order.owner, warehouse=order.warehouse, biz_date=order.biz_date
        ),
        task_type="RECEIVE",
        source_app="inbound",
        source_model="InboundOrder",
        source_pk=str(order.pk),
        ref_no=order.order_no,
        created_by=user,
    )
    if not existing_task:
        totals = order_quantities(order)
        for row in order.lines.filter(is_deleted=False).select_related("product"):
            values = totals[row.pk]
            remaining = max(
                ZERO,
                row.base_qty
                - values["ok"]
                - values["damage"]
                - values["reject"]
                - values["pending"],
            )
            if remaining:
                WmsTaskLine.objects.create(
                    task=task,
                    product=row.product,
                    qty_plan=remaining,
                    src_model="inbound.InboundOrderLine",
                    src_id=row.pk,
                )
    task_services.task_release(request=SimpleNamespace(user=user), task=task)
    receipt = InboundReceipt.objects.create(
        order=order,
        owner=order.owner,
        warehouse=order.warehouse,
        supplier=order.supplier,
        receipt_no=task.task_no,
        order_no_snap=order.order_no,
        biz_date=timezone.now().date(),
        task=task,
        stage="DRAFT",
    )
    event(order, user, "batch-create", receipt=receipt)
    return receipt


def order_action(user, pk, action, data):
    if action in {"submit", "withdraw"}:
        require(user, {"owner_salesperson"}, "inbound.submit_as_owner_buyers")
    elif action.startswith("owner-"):
        require(user, {"owner_manager"}, "inbound.approve_as_owner_manager")
    elif action == "batch-create":
        require(
            user,
            {"warehouse_manager", "warehouse_operator"},
            (
                "inbound.view_inboundorder"
                if "warehouse_manager" in scope_for(user).roles
                else "tasking.claim_task_as_wh_operator"
            ),
        )
    else:
        require(user, {"warehouse_manager"}, "inbound.manage_arrival")

    if action in {"warehouse-confirm", "warehouse-reject"}:
        require(user, {"warehouse_manager"}, "inbound.approve_as_wh_manager")

    def operation():
        if action in {"warehouse-confirm", "batch-create"}:
            from allapp.inventory.locking import lock_warehouses_for_inventory_write

            # Task/receipt inserts also lock their warehouse foreign key. Match posting's
            # warehouse -> order order so a later arrival cannot deadlock an earlier posting.
            lock_warehouses_for_inventory_write(order_for(user, pk).warehouse_id)
        order = order_for(user, pk, lock=True)
        check_version(order, data.get("version"))
        if order.receipt_mode != "BATCH" or order.is_closed:
            raise ArrivalConflict("此申请不能执行该动作。")
        reason = data.get("reason", "").strip()
        if action in {"owner-reject", "warehouse-reject", "close"} and not reason:
            raise ValidationError("请填写原因。")
        order._allow_status_write = True
        receipt = None
        if action == "submit":
            if (
                not order.eta
                or not order.delivery_contact.strip()
                or not order.delivery_phone.strip()
            ):
                raise ValidationError("提交时必须填写预计到货时间、送货联系人和电话。")
            if not owner_can_use_warehouse(order.owner_id, order.warehouse_id):
                raise ValidationError("仓库授权已失效。")
            if not order.lines.filter(is_deleted=False, base_qty__gt=0).exists():
                raise ValidationError("至少填写一行有效商品。")
            order.submit_by_owner_buyers(user)
            order.workflow_reason = ""
        elif action == "withdraw":
            if order.submit_status != "SUBMITTED" or order.approval_status not in {
                "OWNER_PENDING",
                "OWNER_APPROVED",
                "WHS_PENDING",
            }:
                raise ArrivalConflict("仓库确认前才能撤回。")
            order.submit_status, order.approval_status = "DRAFT", "NOT_READY"
        elif action in {"owner-approve", "owner-reject"}:
            getattr(order, "owner_approve" if action == "owner-approve" else "owner_reject")(user)
            order.workflow_reason = reason
        elif action == "warehouse-reject":
            order.wh_reject(user)
            order.workflow_reason = reason
        elif action == "warehouse-confirm":
            if not owner_can_use_warehouse(order.owner_id, order.warehouse_id):
                raise ValidationError("货主与仓库授权已失效。")
            order.wh_confirm(user)
            task = WmsTask.objects.get(
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(order.pk),
                task_type="RECEIVE",
                status="DRAFT",
            )
            receipt = new_batch(order, user, existing_task=task)
        elif action == "batch-create":
            if order.approval_status != "WHS_APPROVED":
                raise ArrivalConflict("仓库尚未确认申请。")
            receipt = new_batch(order, user)
        elif action == "close":
            if order.approval_status != "WHS_APPROVED" or order.receiving_closed_at:
                raise ArrivalConflict("当前不能结束收货。")
            # Empty, unclaimed placeholder batches may be cancelled on zero-arrival close.
            for candidate in order.receipts.filter(
                stage__in=["DRAFT", "REJECTED", "PENDING", "APPROVED"]
            ):
                if (
                    candidate.stage == "DRAFT"
                    and not candidate.lines.exists()
                    and not TaskAssignment.objects.filter(
                        task_id=candidate.task_id, finished_at__isnull=True
                    ).exists()
                ):
                    candidate.stage = "CANCELLED"
                    candidate.save(update_fields=["stage"])
                    WmsTask.objects.filter(pk=candidate.task_id).update(status="CANCELLED")
                    WmsTaskLine.objects.filter(task_id=candidate.task_id).update(status="CANCELLED")
                else:
                    raise ArrivalConflict("请先处理尚未提交、退回、待审核或待入账的验收批次。")
            order.receiving_closed_at = timezone.now()
            order.workflow_reason = reason
        elif action == "note":
            if not reason:
                raise ValidationError("请填写说明。")
        else:
            raise ValidationError("不支持的动作。")
        order.version += 1
        order.save()
        event(order, user, action, receipt=receipt, reason=reason)
        update_completion(order)
        return {"order_id": order.pk, "receipt_id": receipt.pk if receipt else None}

    order_for(user, pk)
    return command(user, data, action, pk, operation)


def owns_task(user, task):
    if not TaskAssignment.objects.filter(
        task=task, assignee=user, finished_at__isnull=True
    ).exists():
        raise PermissionDenied("请先领取任务，不能操作他人的任务。")


def task_action(user, order, receipt, task, action, data):
    require(user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
    if action == "claim":
        task_services.claim_task(
            task, by_user=user, allowed_wh_ids={order.warehouse_id}, arrival=True
        )
    else:
        owns_task(user, task)
        task_services.task_start(request=SimpleNamespace(user=user), task=task, arrival=True)
    if task.pk == receipt.task_id:
        receipt.made_by = user
    bump(receipt)
    receipt.save(update_fields=["made_by"])
    event(order, user, action, receipt=receipt, snapshot={"task_id": task.pk})


def save_batch(user, order, receipt, data):
    require(user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
    owns_task(user, receipt.task)
    if receipt.stage not in {"DRAFT", "REJECTED"}:
        raise ArrivalConflict("仅验收草稿或退回批次可以修改。")
    originals = {
        r.pk: r for r in order.lines.filter(is_deleted=False).select_related("product__base_uom")
    }
    locations = {
        r.pk: r
        for r in Location.objects.filter(
            pk__in={r["location_id"] for r in data["lines"]},
            warehouse_id=order.warehouse_id,
            is_disabled=False,
            is_frozen=False,
            is_deleted=False,
        )
    }
    existing = {r.pk: r for r in receipt.lines.filter(is_deleted=False)}
    used, serials = set(), set()
    next_line = max((r.line_no for r in existing.values()), default=0)
    for payload in data["lines"]:
        original = originals.get(payload["order_line_id"])
        if not original or payload["location_id"] not in locations:
            raise ValidationError("订单行或收货库位不在授权范围内。")
        product = original.product
        row_id = payload.get("id")
        if row_id and (row_id not in existing or row_id in used):
            raise ValidationError("验收行关联无效。")
        next_line += 10
        row = existing[row_id] if row_id else InboundReceiptLine(receipt=receipt, line_no=next_line)
        if row_id:
            used.add(row_id)
        total = payload["qty_ok"] + payload["qty_damage"] + payload["qty_reject"]
        if total <= 0:
            raise ValidationError("每行本次到货数量必须大于零；未到货行不要加入本批。")
        if payload["qty_damage"] and not payload.get("damage_reason", "").strip():
            raise ValidationError("破损原因必填，照片选填。")
        if payload["qty_reject"] and not payload.get("reject_reason", "").strip():
            raise ValidationError("拒收原因必填。")
        serial = payload.get("serial_no", "").strip().upper()
        if product.serial_control:
            if not serial or total != 1 or (product.pk, serial) in serials:
                raise ValidationError("序列号商品必须逐件登记，每个序列号只能出现一次。")
            serials.add((product.pk, serial))
        elif serial:
            raise ValidationError("非序列号商品不能填写序列号。")
        if product.batch_control and not payload.get("lot_no", "").strip():
            raise ValidationError("批次管理商品必须填写批次。")
        if product.expiry_control and not payload.get("exp_date"):
            raise ValidationError("效期管理商品必须填写到期日。")
        if (
            payload.get("mfg_date")
            and payload.get("exp_date")
            and payload["mfg_date"] > payload["exp_date"]
        ):
            raise ValidationError("到期日不能早于生产日期。")
        row.order_line = original
        row.product = product
        row.product_code, row.product_name, row.spec = product.code, product.name, product.spec
        row.base_uom = original.base_uom or product.base_uom.code
        row.unit_price, row.order_qty = original.base_price, original.base_qty
        row.base_qty = payload["qty_ok"] + payload["qty_damage"]
        row.location = locations[payload["location_id"]]
        for field in (
            "qty_ok",
            "qty_damage",
            "qty_reject",
            "lot_no",
            "mfg_date",
            "exp_date",
            "damage_reason",
            "reject_reason",
        ):
            setattr(row, field, payload.get(field))
        row.serial_no = serial
        row.save()
    removed = set(existing) - used
    if ArrivalPhoto.objects.filter(line_id__in=removed).exists():
        raise ValidationError("请先删除未提交照片再移除验收行。")
    receipt.lines.filter(pk__in=removed).delete()
    receipt.made_by = user
    receipt.reason = data.get("reason", "")
    receipt.stage = "DRAFT"
    receipt.save()
    if receipt.task.status == "RELEASED":
        task_services.task_start(
            request=SimpleNamespace(user=user), task=receipt.task, arrival=True
        )
        event(order, user, "start", receipt=receipt, snapshot={"task_id": receipt.task_id})
    event(
        order, user, "batch-edit", receipt=receipt, snapshot={"line_count": receipt.lines.count()}
    )


def receipt_unit(row):
    unit = row.product.base_uom
    return unit.name if row.base_uom == unit.code else row.base_uom


def batch_snapshot(receipt):
    return [
        {
            "id": r.pk,
            "order_line_id": r.order_line_id,
            "product_name": r.product_name,
            "unit": receipt_unit(r),
            "base_unit": r.base_uom,
            "qty_ok": str(r.qty_ok),
            "qty_damage": str(r.qty_damage),
            "qty_reject": str(r.qty_reject),
            "lot_no": r.lot_no,
            "mfg_date": str(r.mfg_date or ""),
            "exp_date": str(r.exp_date or ""),
            "serial_no": r.serial_no,
            "location": r.location.code if r.location else "未记录",
            "damage_reason": r.damage_reason,
            "reject_reason": r.reject_reason,
            "photo_ids": [p.pk for p in r.arrival_photos.all()],
        }
        for r in receipt.lines.select_related("location", "product__base_uom").prefetch_related(
            "arrival_photos"
        )
    ]


def batch_action(user, order_id, receipt_id, action, data):
    # Access is checked even when replaying a previously successful command.
    order_for(user, order_id)
    if action in {
        "batch-approve",
        "batch-reject",
        "retry-posting",
        "putaway-approve",
        "putaway-reject",
    }:
        require(user, {"warehouse_manager"}, "inbound.manage_arrival")
    elif action == "note":
        if "warehouse_manager" in scope_for(user).roles:
            require(user, {"warehouse_manager"}, "inbound.manage_arrival")
        else:
            require(user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")
    else:
        require(user, {"warehouse_operator"}, "tasking.claim_task_as_wh_operator")

    def operation():
        order = order_for(user, order_id, lock=True)
        receipt = receipt_for(user, order, receipt_id, lock=True)
        check_version(receipt, data.get("version"))
        reason = data.get("reason", "").strip()
        task = WmsTask.objects.select_for_update().get(pk=receipt.task_id)
        if action in {"claim", "start", "putaway-claim", "putaway-start"}:
            target = receipt.putaway_task if action.startswith("putaway-") else task
            if not target:
                raise ArrivalConflict("尚无上架任务。")
            task_action(user, order, receipt, target, action.split("-")[-1], data)
            return {"order_id": order.pk, "receipt_id": receipt.pk}
        if action == "batch-edit":
            save_batch(user, order, receipt, data)
        elif action == "batch-submit":
            owns_task(user, task)
            if receipt.stage not in {"DRAFT", "REJECTED"} or not receipt.lines.exists():
                raise ArrivalConflict("验收批次尚未填写或不能提交。")
            serial_filter = Q(pk__in=[])
            for row in receipt.lines.select_related("product__base_uom"):
                if row.base_uom != row.product.base_uom.code:
                    raise ValidationError("商品基本单位已变化，请核对后重新登记。")
                if row.serial_no and row.qty_ok + row.qty_damage > 0:
                    serial_filter |= Q(product_id=row.product_id, serial_no=row.serial_no)
            if serial_filter != Q(pk__in=[]):
                from allapp.inventory.models import InventoryDetail

                if InventoryDetail.objects.filter(
                    serial_filter, owner_id=order.owner_id, is_active=True, onhand_qty__gt=0
                ).exists():
                    raise ValidationError("序列号已在库，不能重复收货。")
                if (
                    InboundReceiptLine.objects.filter(
                        serial_filter, receipt__owner_id=order.owner_id, receipt__stage__in=ACTIVE
                    )
                    .exclude(receipt=receipt)
                    .filter(Q(qty_ok__gt=0) | Q(qty_damage__gt=0))
                    .exists()
                ):
                    raise ValidationError("序列号已在其他有效验收批次中登记。")
            totals = order_quantities(order)
            own = defaultdict(lambda: ZERO)
            for row in receipt.lines.select_related("order_line"):
                own[row.order_line_id] += row.qty_ok + row.qty_damage + row.qty_reject
                known = totals[row.order_line_id]
                if (
                    own[row.order_line_id]
                    + sum(known[k] for k in ("ok", "damage", "reject", "pending"))
                    > row.order_line.base_qty
                    and not receipt.reason.strip()
                ):
                    raise ValidationError("存在超收，请填写本批差异原因。")
            # Keep plan rows for traceability, each actual batch row gets an explicit task line.
            task.lines.filter(arrival_receipt_line__isnull=True).update(status="CANCELLED")
            for row in receipt.lines.select_related("product", "order_line"):
                line = row.task_line or WmsTaskLine(
                    task=task,
                    product=row.product,
                    src_model="inbound.InboundOrderLine",
                    src_id=row.order_line_id,
                )
                line.qty_plan = row.qty_ok + row.qty_damage + row.qty_reject
                line.qty_done = line.qty_plan
                line.status, line.finished_at, line.finished_by = "COMPLETED", timezone.now(), user
                line.save()
                row.task_line = line
                row.save(update_fields=["task_line"])
            receipt.stage, receipt.submit_status = "PENDING", "SUBMITTED"
            receipt.submitted_at = timezone.now()
            receipt.save()
            WmsTask.objects.filter(pk=task.pk).update(
                status="COMPLETED", review_status="PENDING", finished_at=timezone.now()
            )
            ArrivalPhoto.objects.filter(line__receipt=receipt).update(sealed=True)
            event(
                order,
                user,
                action,
                receipt=receipt,
                reason=receipt.reason,
                snapshot={"lines": batch_snapshot(receipt)},
            )
        elif action in {"batch-approve", "batch-reject"}:
            if receipt.stage != "PENDING" or receipt.made_by_id == user.pk:
                raise ArrivalConflict("只能审核待审批次，且不能审核本人作业。")
            if action == "batch-reject":
                if not reason:
                    raise ValidationError("退回原因必填。")
                receipt.stage = "REJECTED"
                receipt.reason = reason
                WmsTask.objects.filter(pk=task.pk).update(
                    status="IN_PROGRESS", review_status="NONE", posting_status="NONE"
                )
            else:
                other = order_quantities(
                    order, order.receipts.exclude(pk=receipt.pk).prefetch_related("lines")
                )
                own = defaultdict(lambda: ZERO)
                over = False
                for row in receipt.lines.select_related("order_line"):
                    own[row.order_line_id] += row.qty_ok + row.qty_damage + row.qty_reject
                    v = other[row.order_line_id]
                    over |= (
                        own[row.order_line_id] + v["ok"] + v["damage"] + v["reject"]
                        > row.order_line.base_qty
                    )
                if over and not data.get("approve_overage"):
                    raise ValidationError("本批含超收，需明确勾选批准超收。")
                receipt.stage, receipt.approved_by, receipt.approved_at = (
                    "APPROVED",
                    user,
                    timezone.now(),
                )
                task_services.approve_task(task.pk, by_user=user, note=reason, arrival=True)
            receipt.save()
            event(
                order,
                user,
                action,
                receipt=receipt,
                reason=reason,
                snapshot={"approve_overage": bool(data.get("approve_overage"))},
            )
        elif action == "retry-posting":
            if receipt.stage != "APPROVED" and not (
                receipt.putaway_task_id and receipt.putaway_task.review_status == "APPROVED"
            ):
                raise ArrivalConflict("没有可重试的已审核批次。")
            event(order, user, action, receipt=receipt, reason=reason)
        elif action == "putaway-record":
            from .arrival_posting import record_putaway

            record_putaway(order, receipt, user, data)
        elif action in {"putaway-approve", "putaway-reject"}:
            target = receipt.putaway_task
            if not target or target.status != "COMPLETED" or target.review_status != "PENDING":
                raise ArrivalConflict("上架任务尚未完成或已审核。")
            if TaskAssignment.objects.filter(task=target, assignee=user).exists():
                raise PermissionDenied("不能审核本人上架作业。")
            if action == "putaway-reject":
                if not reason:
                    raise ValidationError("退回原因必填。")
                target.status, target.review_status = "IN_PROGRESS", "NONE"
                target.save(update_fields=["status", "review_status"])
                target.lines.update(qty_done=0, status="IN_PROGRESS", finished_at=None)
                event(order, user, action, receipt=receipt, reason=reason)
            else:
                task_services.approve_task(target.pk, by_user=user, note=reason, arrival=True)
                event(order, user, action, receipt=receipt, reason=reason)
        elif action == "note":
            if not reason:
                raise ValidationError("说明不能为空。")
            event(order, user, "note", receipt=receipt, reason=reason)
        else:
            raise ValidationError("不支持的动作。")
        bump(receipt)
        bump(order)
        return {
            "order_id": order.pk,
            "receipt_id": receipt.pk,
            "post_putaway": action == "putaway-approve"
            or (action == "retry-posting" and receipt.stage == "POSTED"),
        }

    return command(user, data, action, [order_id, receipt_id], operation)


def update_completion(order):
    """Complete only when every batch and every derived putaway is settled."""
    receipts = list(
        order.receipts.exclude(stage__in=["LEGACY", "CANCELLED"])
        .prefetch_related("lines")
        .select_related("putaway_task")
    )
    totals = order_quantities(order, receipts)
    lines = list(order.lines.filter(is_deleted=False))
    fulfilled = bool(lines) and all(
        sum(totals[r.pk][k] for k in ("ok", "damage", "reject")) >= r.base_qty for r in lines
    )
    unresolved = any(r.stage not in {"POSTED", "NO_STOCK"} for r in receipts)
    if fulfilled and not unresolved and not order.receiving_closed_at:
        order.receiving_closed_at = timezone.now()
        order.save(update_fields=["receiving_closed_at"])
    putaway_done = all(
        r.stage == "NO_STOCK" or (r.putaway_task_id and r.putaway_task.posting_status == "POSTED")
        for r in receipts
    )
    if order.receiving_closed_at and not unresolved and putaway_done and not order.is_closed:
        order.is_closed = True
        order.close_reason = "收货结束，全部批次处理及上架完成"
        order.save(update_fields=["is_closed", "close_reason"])
