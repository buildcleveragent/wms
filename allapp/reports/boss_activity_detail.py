"""Explicit safe projections for Boss drilldowns; never expose raw model JSON."""

from django.core.exceptions import ValidationError as ModelValidationError
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from allapp.accounts.models import AuditEvent
from allapp.billing.models import BillingAccrual, PaymentReceipt
from allapp.core.product_identity import identity_display_name
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.pos.inventory_trace import resolve_sale_issue_layers
from allapp.tasking.models import WmsTask

from .boss_activity import actor_name, find_activity, pos_inventory, summarize
from .product_display import product_display_fields


def fields(**values):
    return [{"label": key, "value": value} for key, value in values.items() if value is not None]


def product_title(row):
    snapshot = getattr(row, "product_snapshot", None)
    if snapshot:
        return identity_display_name(snapshot)
    product = getattr(row, "product", None)
    return product_display_fields(product)["product_display_name"] if product else "未记录商品"


def line_row(row):
    product = getattr(row, "product", None)
    source = row
    if getattr(row, "outbound_order_line_id", None):
        source = row.outbound_order_line
    elif getattr(row, "sale_line_id", None):
        source = row.sale_line.outbound_order_line or row
    if hasattr(source, "base_uom_id"):
        unit = source.base_uom.name if source.base_uom_id else "历史单位未记录"
    else:
        unit = (
            getattr(row, "base_unit", None)
            or (
                getattr(row, "base_uom", None)
                if isinstance(getattr(row, "base_uom", None), str)
                else None
            )
            or (
                f"历史单位未记录；当前档案为 {product.base_uom.name}"
                if product and product.base_uom_id
                else "单位未记录"
            )
        )
    planned = getattr(row, "qty_plan", getattr(row, "base_qty", getattr(row, "qty", None)))
    done = getattr(row, "qty_done", getattr(row, "done", None))
    return {
        "id": row.pk,
        "title": product_title(row),
        "fields": fields(
            商品编码=getattr(product, "code", ""),
            数量=planned,
            单位=unit,
            已完成=done,
            剩余待完成=max(0, planned - done) if planned is not None and done is not None else None,
            金额=getattr(row, "amount", None),
            批次=getattr(row, "batch_no", None),
        ),
    }


def inventory_row(row):
    # Current balance is labelled separately; no invented historical before/after.
    current = InventoryDetail.objects.filter(
        owner_id=row.owner_id,
        warehouse_id=row.warehouse_id,
        product_id=row.product_id,
        location_id=row.location_id,
        batch_no=row.batch_no,
        serial_no=row.serial_no,
        base_unit=row.base_unit,
    ).aggregate(qty=Sum("onhand_qty"))["qty"]
    return {
        "id": row.pk,
        "title": product_title(row),
        "fields": fields(
            本次增减=row.qty_delta,
            单位=row.base_unit,
            类型=row.get_tx_type_display(),
            库位=row.location.code,
            批次=row.batch_no,
            当前该库位批次库存=current if current is not None else "未找到同单位库存记录",
            过账时间=row.posted_at,
            来源单号=row.src_no,
            配对号=str(row.pair_id) if row.pair_id else None,
            原因=row.memo,
        ),
    }


def stock_qs(qs):
    return qs.select_related("product__brand", "product__base_uom", "location").order_by(
        "-posted_at", "-id"
    )


def waiting_text(row, ended=False):
    if ended:
        return None
    created = getattr(row, "created_at", None)
    if not created:
        return "历史开始时间未记录"
    minutes = max(0, int((timezone.now() - created).total_seconds() // 60))
    return f"{minutes // 60} 小时 {minutes % 60} 分钟（自创建起）"


def task_row(task):
    return {
        "id": task.pk,
        "title": f"{task.get_task_type_display()} · {task.task_no}",
        "target": {"kind": "task_detail", "id": task.pk},
        "fields": fields(
            状态=task.get_status_display(),
            来源单号=task.ref_no,
            计划完成=task.planned_end or "未设期限",
            未完成时长=waiting_text(task, task.status in {"COMPLETED", "CANCELLED"}),
            创建时间=task.created_at,
            开始时间=task.started_at,
            完成时间=task.finished_at,
            过账状态=task.get_posting_status_display(),
            过账时间=task.posted_at,
        ),
    }


def status_row(row):
    return {
        "id": row.pk,
        "title": f"{row.get_old_status_display()} → {row.get_new_status_display()}",
        "fields": fields(时间=row.changed_at, 操作人=actor_name(row.changed_by), 备注=row.note),
    }


def scan_row(row):
    return {
        "id": row.pk,
        "title": product_title(row),
        "fields": fields(
            时间=row.created_at,
            操作人=actor_name(row.by_user),
            基本数量变化=row.qty_base_delta,
            当前档案基本单位=(
                row.product.base_uom.code if row.product and row.product.base_uom_id else "未知"
            ),
            扫码数量=row.qty_aux,
            扫码单位=row.uom_name or row.uom_code,
            结果=row.get_status_display(),
            批次=row.lot_no,
            库位=row.location.code if row.location else None,
            过账时间=row.posted_at,
            备注=row.error_msg or row.remark,
        ),
    }


def audit_row(row):
    labels = {
        "outbound.order.create": "创建出库单",
        "outbound.order.submit": "提交出库单",
        "outbound.order.owner_approve": "货主审核通过",
        "outbound.order.owner_reject": "货主退回",
        "outbound.order.warehouse_confirm": "仓库确认",
        "outbound.order.warehouse_reject": "仓库退回",
        "outbound.order.withdraw": "撤回",
        "outbound.order.cancel": "取消",
        "outbound.assisted.create_release": "仓库代办出库",
    }
    return {
        "id": row.pk,
        "title": labels.get(row.action, row.action),
        "fields": fields(时间=row.occurred_at, 操作人=actor_name(row.actor)),
    }


def task_sections(scope, task):
    return {
        "lines": (
            "任务商品",
            task.lines.select_related("product__brand", "product__base_uom").order_by("id"),
            line_row,
        ),
        "history": (
            "状态记录",
            task.status_logs.select_related("changed_by").order_by("-changed_at", "-id"),
            status_row,
        ),
        "scans": (
            "扫码记录",
            task.scan_logs.select_related(
                "product__brand", "product__base_uom", "by_user", "location"
            ).order_by("-created_at", "-id"),
            scan_row,
        ),
        "inventory": (
            "实际库存流水",
            stock_qs(
                scope.apply(InventoryTransaction.objects.all()).filter(
                    src_model__iexact="WmsTask", src_id=task.pk, posted_at__isnull=False
                )
            ),
            inventory_row,
        ),
        "assignments": (
            "任务领取",
            task.assignments.select_related("assignee").order_by("-created_at", "-id"),
            lambda row: {
                "id": row.pk,
                "title": actor_name(row.assignee),
                "fields": fields(领取时间=row.accepted_at, 结束时间=row.finished_at),
            },
        ),
    }


def order_sections(scope, order):
    from .boss_attention import order_progress_lines

    tasks = scope.apply(WmsTask.objects.all()).filter(
        source_model__in=["OutboundOrder", "outboundorder", "outbound.OutboundOrder"],
        source_pk=str(order.pk),
    )
    return {
        "lines": (
            "出库商品",
            order_progress_lines(OutboundOrderLine)
            .filter(order=order)
            .select_related("product__brand", "product__base_uom", "base_uom")
            .order_by("id"),
            line_row,
        ),
        "tasks": ("作业进度", tasks.order_by("-created_at", "-id"), task_row),
        "history": (
            "审核及提交记录",
            scope.apply(AuditEvent.objects.all())
            .filter(
                object_type="outbound.outboundorder",
                object_id=str(order.pk),
                succeeded=True,
                action__in=[
                    "outbound.order.create",
                    "outbound.order.submit",
                    "outbound.order.owner_approve",
                    "outbound.order.owner_reject",
                    "outbound.order.warehouse_confirm",
                    "outbound.order.warehouse_reject",
                    "outbound.order.withdraw",
                    "outbound.order.cancel",
                    "outbound.assisted.create_release",
                ],
            )
            .select_related("actor")
            .order_by("-occurred_at", "-id"),
            audit_row,
        ),
        "inventory": (
            "实际库存流水",
            stock_qs(
                scope.apply(InventoryTransaction.objects.all()).filter(
                    Q(src_model__iexact="WmsTask", src_id__in=tasks.values("id"))
                    | Q(src_model__iexact="OutboundOrder", src_id=order.pk),
                    posted_at__isnull=False,
                )
            ),
            inventory_row,
        ),
    }


def paginate_sections(sections, params):
    result = {}
    try:
        size = min(100, max(1, int(params.get("page_size", 20))))
        for key, (label, qs, render) in sections.items():
            page = max(1, int(params.get(f"{key}_page", 1)))
            count = qs.count()
            result[key] = {
                "label": label,
                "count": count,
                "page": page,
                "page_size": size,
                "results": [render(row) for row in qs[(page - 1) * size : page * size]],
            }
    except (ValueError, TypeError):
        raise ValidationError({"page": "分页参数必须为整数。"}) from None
    return result


def activity_detail(scope, kind, pk, params):
    warnings = []
    if kind in {"outbound_document", "inbound_document"}:
        from .boss_attention import order_progress_lines

        inbound = kind == "inbound_document"
        model = InboundOrder if inbound else OutboundOrder
        order = scope.apply(model.objects.all()).filter(pk=pk).first()
        if not order:
            raise NotFound()
        header = {
            "id": order.pk,
            "title": order.order_no,
            "fields": fields(
                提交状态=order.get_submit_status_display(),
                审核状态=order.get_approval_status_display(),
                业务期限=(order.eta if inbound else order.etd) or "未设期限",
                已关闭=order.is_closed,
                未完成时长=waiting_text(
                    order, order.is_closed or order.approval_status == "CANCELLED"
                ),
            ),
        }
        if inbound:
            tasks = scope.apply(WmsTask.objects.all()).filter(
                source_model__in=["InboundOrder", "inbound.InboundOrder"], source_pk=str(order.pk)
            )
            sections = {
                "lines": (
                    "入库商品",
                    order_progress_lines(InboundOrderLine)
                    .filter(order=order)
                    .select_related("product__brand", "product__base_uom")
                    .order_by("id"),
                    line_row,
                ),
                "tasks": ("作业进度", tasks.order_by("-created_at", "-id"), task_row),
                "inventory": (
                    "实际库存流水",
                    stock_qs(
                        scope.apply(InventoryTransaction.objects.all()).filter(
                            src_model__iexact="WmsTask",
                            src_id__in=tasks.values("id"),
                            posted_at__isnull=False,
                        )
                    ),
                    inventory_row,
                ),
            }
        else:
            sections = order_sections(scope, order)
        warnings.append("单据、任务为当前状态；缺少历史记录时不能推算过去余额。")
    elif kind == "task_detail":
        task = (
            scope.apply(WmsTask.objects.select_related("owner", "warehouse")).filter(pk=pk).first()
        )
        if not task:
            raise NotFound()
        header = task_row(task)
        sections = task_sections(scope, task)
        warnings.append("状态及扫码列表仅展示已保存的记录，历史日志可能不完整。")
    else:
        source, row = find_activity(scope, kind, pk)
        header = summarize(source, row)
        header["fields"] = []
        if kind == "order":
            order = scope.apply(OutboundOrder.objects.all()).get(pk=int(row.object_id))
            header["fields"] = fields(
                当前提交状态=order.get_submit_status_display(),
                当前审核状态=order.get_approval_status_display(),
                业务日期=order.biz_date,
            )
            sections = order_sections(scope, order)
            warnings.append("以下为当前单据明细及已保存历史；提交本身不表示库存已扣减。")
        elif kind in {"task", "assignment"}:
            header["fields"] = task_row(row.task)["fields"]
            sections = task_sections(scope, row.task)
            warnings.append("状态及扫码列表仅展示已保存的记录，历史日志可能不完整。")
        elif kind == "pos":
            sale = row.sale
            lines = scope.pos_lines(sale)
            selected_count = lines.count()
            partial = selected_count != sale.lines.count()
            is_return = row.action == "RETURN"
            if is_return:
                line_qs = row.return_order.lines.filter(
                    sale_line_id__in=lines.values("id")
                ).select_related(
                    "product__brand",
                    "product__base_uom",
                    "sale_line__outbound_order_line__base_uom",
                )
            else:
                line_qs = lines
            amount = line_qs.aggregate(total=Sum("amount"))["total"]
            header["fields"] = fields(
                销售单号=sale.sale_no,
                当前销售状态=sale.get_status_display(),
                当前筛选商品金额=amount,
                金额口径="原 POS 金额（币种未记录）",
                展示范围="仅所选货主部分" if partial else "整笔销售",
                原因=row.reason,
            )
            stock = pos_inventory(scope, sale, action=row.action, return_id=row.return_order_id)
            try:
                resolve_sale_issue_layers(
                    list(lines.select_related("sale", "outbound_order_line__order"))
                )
            except ModelValidationError:
                warnings.append(
                    "原销售库存追溯记录不完整或不一致；以下仅列可关联流水，不推算缺失库存。"
                )
            sections = {
                "lines": ("商品明细", line_qs.order_by("id"), line_row),
                "inventory": ("本次活动实际库存流水", stock_qs(stock), inventory_row),
                "tasks": (
                    "关联作业任务",
                    scope.apply(WmsTask.objects.all())
                    .filter(
                        source_app__iexact="pos",
                        source_pk__in=sale.sale_orders.values("outbound_order_id"),
                    )
                    .order_by("-created_at", "-id"),
                    task_row,
                ),
                "orders": (
                    "关联出库单",
                    scope.apply(
                        sale.sale_orders.select_related("outbound_order"),
                        warehouse="sale__warehouse_id",
                    ).order_by("id"),
                    lambda link: {
                        "id": link.pk,
                        "title": link.outbound_order.order_no,
                        "fields": fields(
                            当前审核状态=link.outbound_order.get_approval_status_display(),
                            金额=link.amount,
                        ),
                    },
                ),
                "history": (
                    "销售及退货记录",
                    scope.pos_logs().filter(sale=sale).order_by("-created_at", "-id"),
                    lambda log: {
                        "id": log.pk,
                        "title": log.get_action_display(),
                        "fields": fields(
                            时间=log.created_at, 操作人=actor_name(log.actor), 原因=log.reason
                        ),
                        "target": {"kind": "pos", "id": log.pk},
                    },
                ),
            }
            if partial:
                warnings.append("整单收付款无法准确拆分到货主，当前不展示整单收付款金额。")
            else:
                payments = row.return_order.refunds.all() if is_return else sale.payment_lines.all()
                sections["payments"] = (
                    "退款状态" if is_return else "收款状态",
                    payments.order_by("id"),
                    lambda pay: {
                        "id": pay.pk,
                        "title": pay.get_method_display(),
                        "fields": fields(金额=pay.amount, 状态=pay.get_status_display()),
                    },
                )
        elif kind == "inventory":
            stock = scope.apply(InventoryTransaction.objects.all()).filter(pk=pk)
            if row.pair_id:
                stock = scope.apply(InventoryTransaction.objects.all()).filter(
                    pair_id=row.pair_id, posted_at__isnull=False
                )
                warnings.append("配对移位显示移出和移入；同仓同货主移动不增加或减少仓库总库存。")
            sections = {"inventory": ("库存增减", stock_qs(stock), inventory_row)}
            if row.src_model.lower() == "wmstask":
                tasks = scope.apply(WmsTask.objects.all()).filter(pk=row.src_id)
                sections["tasks"] = ("来源任务", tasks, task_row)
        elif kind == "receipt":
            header["fields"] = fields(
                币种=row.currency,
                金额=row.amount,
                业务日期=row.receipt_date,
                当前状态=row.status,
                原收款单=row.reversal_of_id,
                冲销时间=row.reversed_at,
            )
            sections = {
                "reversals": (
                    "原收款及冲销记录",
                    scope.apply(PaymentReceipt.objects.all())
                    .filter(Q(pk=row.reversal_of_id) | Q(reversal_of_id=row.pk))
                    .order_by("posted_at", "id"),
                    lambda receipt: {
                        "id": receipt.pk,
                        "title": receipt.receipt_no,
                        "fields": fields(
                            类型="冲销" if receipt.reversal_of_id else "原收款",
                            金额=receipt.amount,
                            币种=receipt.currency,
                            过账时间=receipt.posted_at,
                        ),
                        "target": {"kind": "receipt", "id": receipt.pk},
                    },
                ),
                "allocations": (
                    "账单核销",
                    row.allocations.select_related("bill").order_by("allocated_at", "id"),
                    lambda allocation: {
                        "id": allocation.pk,
                        "title": allocation.bill.invoice_no,
                        "fields": fields(
                            金额=allocation.amount,
                            币种=row.currency,
                            反向核销=allocation.is_reversal,
                            时间=allocation.allocated_at,
                        ),
                        "target": {"kind": "bill", "id": allocation.bill_id},
                    },
                ),
            }
        elif kind == "billing":
            header["fields"] = fields(
                服务日期=row.service_date,
                计费数量=row.quantity,
                单位=row.quantity_uom,
                当前计价状态=row.get_pricing_status_display(),
                原因=row.pricing_reason,
            )
            sections = {
                "accruals": (
                    "对应应计",
                    scope.apply(BillingAccrual.objects.filter(event=row)).order_by("id"),
                    lambda acc: {
                        "id": acc.pk,
                        "title": acc.get_charge_type_display(),
                        "fields": fields(
                            金额=acc.amount,
                            币种=acc.currency,
                            状态=acc.get_status_display(),
                            服务日期=acc.service_date,
                        ),
                        "target": {"kind": "accrual", "id": acc.pk},
                    },
                )
            }
        elif kind == "alert":
            case = row.case
            header["fields"] = fields(
                当前状态=case.get_status_display(),
                等级=case.get_severity_display(),
                责任人=actor_name(case.assignee),
                截止时间=case.due_at,
            )
            sections = {
                "history": (
                    "异常处理历史",
                    case.history.select_related("created_by").order_by("-created_at", "-id"),
                    lambda item: {
                        "id": item.pk,
                        "title": item.action,
                        "fields": fields(
                            时间=item.created_at,
                            操作人=actor_name(item.created_by),
                            之前状态=item.from_status,
                            之后状态=item.to_status,
                            备注=item.note,
                        ),
                    },
                )
            }
        else:
            raise NotFound()
    return {"header": header, "sections": paginate_sections(sections, params), "notices": warnings}
