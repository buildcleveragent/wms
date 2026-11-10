"""Today's order-level facts. Never substitute tasks, order totals or guessed currency."""

from decimal import Decimal

from django.db.models import Case, CharField, Count, Exists, F, OuterRef, Subquery, When
from django.db.models.functions import Cast, Collate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from allapp.accounts.models import AuditEvent
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask, WmsTaskLine

from .boss_activity import date_bounds, local_today, sources
from .boss_activity_detail import fields
from .boss_attention import CLOSED, order_progress_lines
from .boss_periods import metric_details, metric_sources, ranged
from .models import AlertCase
from .services_operations import OperationFilters, _inventory_transactions, _shipment_lines

FINANCIAL = {"pos_sales", "pos_returns", "accruals", "receipts"}
NOTICE = "单数按订单去重；部分收发不表示整单完成。缺失历史提交记录不以创建时间补算。"
VALUE_NOTICE = "单据未记录可靠币种，货值不可汇总；参考金额仅在商品明细展示。"


def documents(scope, direction):
    model = InboundOrder if direction == "inbound" else OutboundOrder
    qs = scope.apply(model.objects.filter(is_deleted=False))
    if direction == "outbound":
        qs = qs.filter(pos_sale_order__isnull=True)
    return qs


def pending_documents(scope, direction):
    line_model = InboundOrderLine if direction == "inbound" else OutboundOrderLine
    remaining = order_progress_lines(line_model).filter(
        order_id=OuterRef("pk"), base_qty__gt=F("done")
    )
    orders = documents(scope, direction)
    if direction == "inbound":
        orders = orders.filter(receiving_closed_at__isnull=True)
    return (
        orders.filter(submit_status="SUBMITTED", is_closed=False)
        .exclude(approval_status__in=["CANCELLED", "OWNER_REJECTED", "WHS_REJECTED"])
        .filter(Exists(remaining))
    )


def submissions(scope, direction, day):
    left, right = date_bounds(day, day)
    if direction == "outbound":
        audits = next(s.qs for s in sources(scope) if s.key == "order")
    else:
        audits = scope.apply(
            AuditEvent.objects.filter(
                object_type="inbound.inboundorder",
                succeeded=True,
                action__in=[
                    "inbound.order.submit",
                    "inbound.order.submitted",
                    "inbound.arrival.submit",
                    "inbound.arrival.SUBMIT",
                ],
            )
        )
    audits = audits.filter(occurred_at__gte=left, occurred_at__lt=right)
    return (
        documents(scope, direction)
        .annotate(
            _text_id=Collate(Cast("pk", CharField()), "utf8mb4_unicode_ci"),
        )
        .filter(_text_id__in=audits.values("object_id"))
        .annotate(
            event_at=Subquery(
                audits.filter(object_id=OuterRef("_text_id"))
                .order_by("-occurred_at", "-id")
                .values("occurred_at")[:1]
            )
        )
    )


def executions(scope, direction, day):
    """Resolve document identities in SQL; line identity must also match product and scope."""
    inbound = direction == "inbound"
    filters = OperationFilters(
        start_date=day, end_date=day, owner_id=scope.owner_id, warehouse_id=scope.warehouse_id
    )
    if inbound:
        qs = _inventory_transactions(direction, filters, scope.access, scope.user).filter(
            qty_delta__gt=0
        )
        tasks = WmsTask.objects.filter(pk=OuterRef("src_id"))
        lines = WmsTaskLine.objects.filter(
            pk=OuterRef("src_line_id"),
            task_id=OuterRef("src_id"),
            product_id=OuterRef("product_id"),
        )
        qs = qs.annotate(
            _source_model=Subquery(tasks.values("source_model")[:1]),
            _source_pk=Subquery(tasks.values("source_pk")[:1]),
            _line_model=Subquery(lines.values("src_model")[:1]),
            _line_pk=Subquery(lines.values("src_id")[:1]),
            event_at=F("posted_at"),
            actual_qty=F("qty_delta"),
            _owner=F("owner_id"),
            _warehouse=F("warehouse_id"),
        )
    else:
        qs = (
            _shipment_lines(filters, scope.access, scope.user)
            .filter(qty_done__gt=0)
            .exclude(task__source_app__iexact="pos")
        )
        # Legacy POS dispatches may use the ordinary outbound source_app. The
        # explicit sale/order relationship is authoritative even in that case.
        pos_ids = (
            scope.apply(OutboundOrder.objects.filter(pos_sale_order__isnull=False))
            .annotate(text_id=Collate(Cast("pk", CharField()), "utf8mb4_unicode_ci"))
            .values("text_id")
        )
        qs = qs.exclude(
            task__source_model__in=["OutboundOrder", "outbound.OutboundOrder"],
            task__source_pk__in=pos_ids,
        )
        qs = qs.annotate(
            _source_model=F("task__source_model"),
            _source_pk=F("task__source_pk"),
            _line_model=F("src_model"),
            _line_pk=F("src_id"),
            event_at=F("task__finished_at"),
            actual_qty=F("qty_done"),
            _owner=F("task__owner_id"),
            _warehouse=F("task__warehouse_id"),
        )
    model = InboundOrder if inbound else OutboundOrder
    valid = (
        documents(scope, direction)
        .annotate(_pk_string=Collate(Cast("pk", CharField()), "utf8mb4_unicode_ci"))
        .filter(
            _pk_string=OuterRef("_source_pk"),
            owner_id=OuterRef("_owner"),
            warehouse_id=OuterRef("_warehouse"),
        )
    )
    # Invalid source types must not accidentally resolve to an unrelated order with the same PK.
    return qs.annotate(
        document_id=Case(
            When(
                _source_model__in=[model.__name__, f"{model._meta.app_label}.{model.__name__}"],
                then=Subquery(valid.values("id")[:1]),
            ),
            default=None,
        )
    ).order_by()


def execution_summary(qs):
    result = qs.aggregate(
        count=Count("document_id", distinct=True), records=Count("pk"), linked=Count("document_id")
    )
    return {
        "count": result["count"],
        "unlinked_records": result["records"] - result["linked"],
        "money": [],
        "value_status": "UNAVAILABLE" if result["records"] else "NO_RECORDS",
        "value_notice": VALUE_NOTICE,
        "execution_records": result["records"],
    }


def overdue_cases(scope):
    # The checker persists business deadline cases; handling deadlines also count directly.
    from django.db.models import Q

    return (
        scope.apply(AlertCase.objects.all())
        .exclude(status__in=CLOSED)
        .filter(
            Q(due_at__lt=timezone.now())
            | Q(alert_type__in=["DEADLINE_INBOUND", "DEADLINE_OUTBOUND", "DEADLINE_TASK"])
        )
    )


def today_summary(scope):
    day = local_today()
    orders = {}
    for direction in ("inbound", "outbound"):
        orders[direction] = {
            **execution_summary(executions(scope, direction, day)),
            "submitted": submissions(scope, direction, day).count(),
        }
    financial = []
    for metric in metric_sources(scope, day, day):
        if metric.key not in FINANCIAL:
            continue
        from django.db.models import Sum

        qs = ranged(metric, day, day).order_by()
        amounts = list(qs.values(metric.currency).annotate(amount=Sum(metric.money)))
        financial.append(
            {
                "key": metric.key,
                "count": qs.count(),
                "money": [
                    {"currency": x[metric.currency], "amount": x["amount"]}
                    for x in amounts
                    if x[metric.currency] not in (None, "", "UNKNOWN")
                ],
                "unknown_currency": any(
                    x[metric.currency] in (None, "", "UNKNOWN") for x in amounts
                ),
            }
        )
    return {
        "business_today": day,
        "orders": orders,
        "financial": financial,
        "backlog": {
            "inbound": pending_documents(scope, "inbound").count(),
            "outbound": pending_documents(scope, "outbound").count(),
            "overdue": overdue_cases(scope).count(),
        },
        "notices": [NOTICE, VALUE_NOTICE],
    }


def execution_fields(scope, direction, records):
    """Only inspect the paginated records; never match a product to an arbitrary order line."""
    inbound = direction == "inbound"
    model = InboundOrderLine if inbound else OutboundOrderLine
    candidates = (
        scope.apply(model.objects.all(), owner="order__owner_id", warehouse="order__warehouse_id")
        .filter(pk__in=[r._line_pk for r in records if r._line_pk])
        .select_related("product__base_uom", "order")
    )
    mapping = {line.pk: line for line in candidates}
    rows = []
    for record in records:
        line = mapping.get(record._line_pk)
        valid = (
            line
            and line.order_id == record.document_id
            and line.product_id == record.product_id
            and record._line_model in [model.__name__, f"{model._meta.app_label}.{model.__name__}"]
        )
        price = line.base_price if valid and (inbound or line.is_priced) else None
        # Inbound zero is the default and carries no evidence of an agreed free price.
        if price == 0 and (
            inbound
            or line.order.pricing_status != "CONFIRMED"
            or line.order.processing_mode != "WAREHOUSE_ASSISTED"
        ):
            price = None
        # Use the source unit snapshot, not a product's potentially changed current unit.
        source_unit = (
            (line.base_uom if inbound else getattr(line.base_uom, "code", "")) if valid else ""
        )
        unit = (record.base_unit if inbound else source_unit) or "单位未记录"
        if not source_unit or (inbound and source_unit != record.base_unit):
            price = None
        rows.append(
            {
                "id": str(record.pk),
                "title": record.product.display_name if record.product else "商品未记录",
                "fields": fields(
                    发生时间=record.event_at,
                    本日实际数量=f"{record.actual_qty} {unit}",
                    单据单价=price,
                    参考金额=(
                        (record.actual_qty * price).quantize(Decimal("0.01"))
                        if price is not None
                        else "缺少可靠价格、来源行或单位"
                    ),
                    币种="未记录",
                    口径="按单据价参考货值" if inbound else "实发数量 × 单据成交价",
                ),
            }
        )
    return rows


def today_details(scope, day, metric, page=1, size=20, document_id=None):
    if metric in FINANCIAL:
        return metric_details(scope, day, day, metric, page, size)
    if metric == "overdue":
        from .boss_attention import case_payload

        qs = overdue_cases(scope).order_by("first_seen_at", "pk")
        rows = []
        for case in qs[(page - 1) * size : page * size]:
            row = case_payload(case)
            rows.append(
                {
                    "id": case.pk,
                    "title": f'{row["record_no"]} · {row["title"]}',
                    "drill": {"type": "case", "id": case.pk},
                    "fields": fields(责任人=row["assignee_name"], 最新进展=row["latest_note"]),
                }
            )
        return {"count": qs.count(), "results": rows}
    parts = metric.split("_")
    if (
        parts[0] not in {"inbound", "outbound"}
        or len(parts) > 2
        or (len(parts) == 2 and parts[1] not in {"submitted", "pending", "unlinked"})
    ):
        raise ValidationError({"metric": "未知今日业务口径。"})
    direction = parts[0]
    mode = parts[1] if len(parts) == 2 else "actual"
    qs = executions(scope, direction, day)
    if mode == "unlinked":
        qs = qs.filter(document_id__isnull=True).order_by("-event_at", "-pk")
        records = list(qs[(page - 1) * size : page * size])
        return {
            "count": qs.count(),
            "results": execution_fields(scope, direction, records),
            "notices": ["缺少可靠订单关联，未计入订单数。", VALUE_NOTICE],
        }
    if document_id:
        records = qs.filter(document_id=document_id).order_by("-event_at", "-pk")
        visible = documents(scope, direction).filter(pk=document_id).exists()
        if not visible:
            from rest_framework.exceptions import NotFound

            raise NotFound()
        return {
            "count": records.count(),
            "results": execution_fields(
                scope, direction, list(records[(page - 1) * size : page * size])
            ),
            "sourceTarget": {"kind": f"{direction}_document", "id": document_id},
            "notices": [VALUE_NOTICE, "以下仅为所选日实绩；完整单据与作业进度请点击来源入口。"],
        }
    if mode == "submitted":
        orders = submissions(scope, direction, day)
    elif mode == "pending":
        orders = pending_documents(scope, direction).annotate(event_at=F("created_at"))
    else:
        orders = (
            documents(scope, direction)
            .filter(pk__in=qs.values("document_id"))
            .annotate(
                event_at=Subquery(
                    qs.filter(document_id=OuterRef("pk"))
                    .order_by("-event_at")
                    .values("event_at")[:1]
                )
            )
        )
    count = orders.count()
    rows = []
    for order in orders.select_related("owner", "warehouse").order_by("-event_at", "-pk")[
        (page - 1) * size : page * size
    ]:
        row = {
            "id": order.pk,
            "title": order.order_no,
            "fields": fields(
                货主=order.owner.name,
                仓库=order.warehouse.name,
                发生时间=order.event_at,
                当前进度="已关闭" if order.is_closed else order.get_approval_status_display(),
            ),
        }
        if mode == "actual":
            row["drill"] = {"type": "today", "metric": metric, "document_id": order.pk}
        else:
            row["target"] = {"kind": f"{direction}_document", "id": order.pk}
        rows.append(row)
    return {
        "count": count,
        "results": rows,
        "page": page,
        "page_size": size,
        "notices": [NOTICE] + ([VALUE_NOTICE] if mode == "actual" else []),
    }
