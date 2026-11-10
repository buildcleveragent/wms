"""Bounded, read-only business activity projections over existing source records.

Each source is scoped, ordered and limited by MySQL before merging. There is no
polling-side write, synthetic event table, or dependency on the reporting ETL.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from functools import cached_property

from django.core import signing
from django.db.models import Exists, OuterRef, Q, Subquery, Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from allapp.accounts.access import AccessScope
from allapp.accounts.models import AuditEvent
from allapp.billing.models import BillingEvent, PaymentReceipt
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder
from allapp.pos.models import PosAuditLog, PosSaleLine
from allapp.tasking.models import TaskAssignment, TaskStatusLog, WmsTask, WmsTaskLine

from .models import AlertCaseHistory

CATEGORIES = {"order", "pos", "inventory", "task", "billing", "receipt", "alert"}
ORDER_ACTIONS = (
    "outbound.order.submit",
    "outbound.order.create",
    "outbound.assisted.create_release",
)
CURSOR_SALT = "boss.activities.v1"


def local_today():
    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


def date_bounds(start, end):
    def midnight(day):
        value = datetime.combine(day, time.min)
        return timezone.make_aware(value) if timezone.is_aware(timezone.now()) else value

    return midnight(start), midnight(end + timedelta(days=1))


def actor_name(user):
    return (user.get_full_name() or user.username) if user else "未记录"


@dataclass
class ActivityScope:
    user: object
    owner_id: int | None = None
    warehouse_id: int | None = None

    @cached_property
    def access(self):
        return AccessScope.for_user(self.user)

    def apply(self, qs, owner="owner_id", warehouse="warehouse_id"):
        qs = self.access.filter_queryset(qs, owner_field=owner, warehouse_field=warehouse)
        if self.owner_id:
            if owner is None:
                return qs.none()
            qs = qs.filter(**{owner: self.owner_id})
        if self.warehouse_id:
            qs = qs.filter(**{warehouse: self.warehouse_id})
        return qs

    def pos_lines(self, sale):
        return self.apply(
            sale.lines.select_related(
                "product__base_uom", "product__brand", "owner", "outbound_order_line__base_uom"
            ),
            warehouse="sale__warehouse_id",
        )

    def pos_logs(self):
        qs = self.access.filter_queryset(
            PosAuditLog.objects.select_related("sale__warehouse", "actor", "return_order"),
            owner_field="sale__lines__owner_id",
            warehouse_field="sale__warehouse_id",
        ).filter(action__in=["CHECKOUT", "RETURN", "VOID"], sale__isnull=False)
        if self.warehouse_id:
            qs = qs.filter(sale__warehouse_id=self.warehouse_id)
        # Exists avoids duplicating a multi-owner receipt in the activity stream.
        lines = PosSaleLine.objects.filter(sale_id=OuterRef("sale_id"))
        if self.owner_id:
            lines = lines.filter(owner_id=self.owner_id)
        if self.access.owner_ids:
            lines = lines.filter(owner_id__in=self.access.owner_ids)
        qs = qs.filter(Exists(lines))
        if self.owner_id or self.access.owner_ids:
            from allapp.pos.models import PosReturnLine

            returns = PosReturnLine.objects.filter(return_order_id=OuterRef("return_order_id"))
            if self.owner_id:
                returns = returns.filter(owner_id=self.owner_id)
            if self.access.owner_ids:
                returns = returns.filter(owner_id__in=self.access.owner_ids)
            qs = qs.annotate(_visible_return=Exists(returns)).filter(
                ~Q(action="RETURN") | Q(_visible_return=True)
            )
        return qs.distinct()


def pos_inventory(scope, sale, *, action="CHECKOUT", return_id=None):
    """Resolve explicit POS links; generic bigint source IDs are never interchangeable."""
    lines = scope.pos_lines(sale)
    if action == "RETURN":
        from allapp.pos.models import PosReturnLine

        ids = PosReturnLine.objects.filter(
            return_order_id=return_id, sale_line_id__in=lines.values("id")
        ).values("id")
        return scope.apply(InventoryTransaction.objects.all()).filter(
            src_model="PosReturnLine", src_id__in=ids, qty_delta__gt=0, posted_at__isnull=False
        )
    legacy = Q(src_model="PosSaleLine", src_id__in=lines.values("id"))
    if action == "VOID":
        return scope.apply(InventoryTransaction.objects.all()).filter(
            legacy, qty_delta__gt=0, posted_at__isnull=False
        )
    task_lines = WmsTaskLine.objects.filter(
        pk=OuterRef("src_line_id"),
        task_id=OuterRef("src_id"),
        src_model__iexact="OutboundOrderLine",
        src_id__in=lines.values("outbound_order_line_id"),
        task__source_app__iexact="pos",
        task__task_type="PICK",
        task__posting_status="POSTED",
    )
    return (
        scope.apply(InventoryTransaction.objects.all())
        .annotate(_pos_line=Exists(task_lines))
        .filter(
            legacy | Q(src_model="WmsTask", _pos_line=True),
            qty_delta__lt=0,
            posted_at__isnull=False,
        )
    )


@dataclass
class Source:
    key: str
    category: str
    qs: object
    timestamp: str
    actor: str
    search: tuple


def sources(scope, *, important=False):
    orders = scope.apply(OutboundOrder.objects.all()).exclude(pos_sale_order__isnull=False)
    audits = scope.apply(AuditEvent.objects.select_related("actor", "owner", "warehouse"))
    audits = audits.filter(
        object_type="outbound.outboundorder", succeeded=True, action__in=ORDER_ACTIONS
    ).filter(
        Q(action__in=["outbound.order.submit", "outbound.assisted.create_release"])
        | Q(action="outbound.order.create", after__submit_status="SUBMITTED")
    )
    # Correlate string audit IDs to real orders without casting arbitrary client input.
    from django.db.models import BigIntegerField
    from django.db.models.functions import Cast

    audits = (
        audits.filter(object_id__regex=r"^[0-9]+$")
        .annotate(_order_id=Cast("object_id", BigIntegerField()))
        .filter(_order_id__in=orders.values("pk"))
        .annotate(
            _order_no=Subquery(orders.filter(pk=OuterRef("_order_id")).values("order_no")[:1])
        )
    )
    yield Source("order", "order", audits, "occurred_at", "actor", ("_order_no",))
    yield Source(
        "pos",
        "pos",
        scope.pos_logs(),
        "created_at",
        "actor",
        ("sale__sale_no", "sale__src_bill_no"),
    )

    pos_tasks = WmsTask.objects.filter(source_app__iexact="pos").values("id")
    stock = (
        scope.apply(
            InventoryTransaction.objects.select_related(
                "warehouse",
                "owner",
                "product__brand",
                "product__base_uom",
                "location",
                "created_by",
            )
        )
        .filter(posted_at__isnull=False)
        .exclude(
            Q(src_model__in=["PosSaleLine", "PosReturnLine"])
            | Q(src_model__iexact="WmsTask", src_id__in=pos_tasks)
        )
    )
    yield Source(
        "inventory",
        "inventory",
        stock,
        "posted_at",
        "created_by",
        ("src_no", "product__code", "product__name"),
    )
    receipts = scope.apply(PaymentReceipt.objects.select_related("warehouse", "owner", "posted_by"))
    yield Source(
        "receipt",
        "receipt",
        receipts.filter(posted_at__isnull=False),
        "posted_at",
        "posted_by",
        ("receipt_no",),
    )
    alerts = scope.apply(
        AlertCaseHistory.objects.select_related("case__warehouse", "case__owner", "created_by"),
        owner="case__owner_id",
        warehouse="case__warehouse_id",
    )
    if important:
        alerts = alerts.filter(case__severity__in=["HIGH", "CRITICAL"])
    yield Source(
        "alert", "alert", alerts, "created_at", "created_by", ("case__title", "case__source_id")
    )
    if important:
        return
    logs = scope.apply(
        TaskStatusLog.objects.select_related("task__warehouse", "task__owner", "changed_by"),
        owner="task__owner_id",
        warehouse="task__warehouse_id",
    ).exclude(task__source_app__iexact="pos")
    yield Source(
        "task", "task", logs, "changed_at", "changed_by", ("task__task_no", "task__ref_no")
    )
    assignments = (
        scope.apply(
            TaskAssignment.objects.select_related("task__warehouse", "task__owner", "assignee"),
            owner="task__owner_id",
            warehouse="task__warehouse_id",
        )
        .filter(accepted_at__isnull=False)
        .exclude(task__source_app__iexact="pos")
    )
    yield Source(
        "assignment",
        "task",
        assignments,
        "accepted_at",
        "assignee",
        ("task__task_no", "task__ref_no"),
    )
    events = scope.apply(BillingEvent.objects.select_related("warehouse", "owner", "task"))
    yield Source(
        "billing", "billing", events, "created_at", "task__posted_by", ("task__task_no", "event_fp")
    )


def summarize(source, row, scope=None):
    kind = source.key
    parent = (
        row.case
        if kind == "alert"
        else row.task if kind in {"task", "assignment"} else row.sale if kind == "pos" else row
    )
    owner = getattr(parent, "owner", None)
    warehouse = parent.warehouse
    actor = row
    for part in source.actor.split("__"):
        actor = getattr(actor, part, None) if actor else None
    title, number, note = "", "", ""
    target = {"kind": kind, "id": row.pk}
    if kind == "order":
        title, number = "新增出库单（正式提交）", row._order_no
        note = "单据提交与库存过账分别记录"
    elif kind == "pos":
        title = {"CHECKOUT": "POS 销售完成", "RETURN": "POS 退货", "VOID": "POS 销售作废"}[
            row.action
        ]
        number = row.return_order.return_no if row.action == "RETURN" else row.sale.sale_no
        note = row.reason or "查看商品及对应库存增减"
        if scope is not None:
            changes = pos_inventory(
                scope, row.sale, action=row.action, return_id=row.return_order_id
            )
            groups = list(
                changes.values("product_id", "product__name", "base_unit")
                .annotate(delta=Sum("qty_delta"))
                .order_by("product_id", "base_unit")[:4]
            )
            if groups:
                note = "；".join(
                    f'{item["product__name"]} {item["delta"]:+f} {item["base_unit"]}'
                    for item in groups[:3]
                )
                if len(groups) > 3:
                    note += "；更多商品见详情"
            else:
                note = "库存追溯记录缺失，请查看详情"
            if row.reason:
                note = f"{row.reason}；{note}"
    elif kind == "inventory":
        title, number = row.get_tx_type_display(), row.src_no
        note = f"{row.product.name} · {row.qty_delta:+f} {row.base_unit} · {row.location.code}"
        if row.pair_id and row.tx_type in {"ISSUE", "RECEIVE", "MOVE_OUT", "MOVE_IN"}:
            title = "库存移位 · " + ("移出" if row.qty_delta < 0 else "移入")
            note += "；配对流水见详情"
    elif kind == "receipt":
        title = "收款冲销" if row.reversal_of_id else "收款过账"
        number, note = row.receipt_no, f"{row.currency} {row.amount}"
    elif kind == "alert":
        title, number = row.case.title, row.case.source_id
        note = {
            "DISCOVER": "发现异常",
            "AUTO_RESOLVE": "来源问题已恢复",
            "CLOSE": "已关闭",
            "ASSIGN": "已指派",
            "ACKNOWLEDGE": "已确认",
            "NOTE": "新增处理备注",
            "REOPEN": "异常再次出现",
        }.get(row.action, row.action)
    elif kind in {"task", "assignment"}:
        title = f"{row.task.get_task_type_display()} · " + (
            "任务领取" if kind == "assignment" else row.get_new_status_display()
        )
        number, note = row.task.task_no, row.task.ref_no
    elif kind == "billing":
        title, number = "新增计费事件", row.task.task_no if row.task else str(row.pk)
        note = f"{row.get_charge_type_display()} · {row.get_pricing_status_display()}"
    return {
        "id": f"{kind}:{row.pk}",
        "category": source.category,
        "title": title,
        "occurred_at": getattr(row, source.timestamp),
        "record_no": number,
        "actor": {"id": getattr(actor, "pk", None), "name": actor_name(actor)},
        "owner": {"id": owner.pk, "name": owner.name} if owner else None,
        "warehouse": {"id": warehouse.pk, "name": warehouse.name},
        "summary": note,
        "target": target,
    }


def activity_page(
    scope,
    *,
    date_from,
    date_to,
    category="",
    actor="",
    search="",
    important=False,
    limit=20,
    cursor="",
    occurred_from=None,
    occurred_until=None,
    action="",
):
    if category and category not in CATEGORIES:
        raise ValidationError({"category": "未知活动类型。"})
    if actor and (not str(actor).isdigit() or int(actor) <= 0):
        raise ValidationError({"actor": "操作人必须为正整数。"})
    if len(search) > 100:
        raise ValidationError({"search": "搜索内容最多 100 字。"})
    if action and action not in {"CHECKOUT", "RETURN", "VOID"}:
        raise ValidationError({"action": "未知 POS 动作。"})
    binding = {
        "user": scope.user.pk,
        "owner": scope.owner_id,
        "warehouse": scope.warehouse_id,
        "access": scope.access.as_dict(),
        "from": str(date_from),
        "to": str(date_to),
        "category": category,
        "actor": str(actor),
        "search": search,
        "important": important,
        "occurred_from": str(occurred_from),
        "occurred_until": str(occurred_until),
        "action": action,
    }
    after = None
    if cursor:
        try:
            data = signing.loads(cursor, salt=CURSOR_SALT, max_age=86400)
            if data["binding"] != binding:
                raise ValueError()
            after = (datetime.fromisoformat(data["time"]), data["source"], int(data["pk"]))
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise ValidationError({"cursor": "分页已失效，请刷新列表。"}) from None
    start, end = date_bounds(date_from, date_to)
    start, end = occurred_from or start, occurred_until or end
    candidates = []
    for source in sources(scope, important=important):
        if category and source.category != category:
            continue
        field = source.timestamp
        qs = source.qs.filter(**{f"{field}__gte": start, f"{field}__lt": end})
        if action:
            if source.key != "pos":
                continue
            qs = qs.filter(action=action)
        if actor:
            qs = qs.filter(**{f"{source.actor}_id": int(actor)})
        if search:
            query = Q()
            for name in source.search:
                query |= Q(**{f"{name}__icontains": search})
            qs = qs.filter(query)
        if after:
            stamp, key, pk = after
            condition = Q(**{f"{field}__lt": stamp})
            if source.key < key:
                condition |= Q(**{field: stamp})
            elif source.key == key:
                condition |= Q(**{field: stamp, "pk__lt": pk})
            qs = qs.filter(condition)
        for row in qs.order_by(f"-{field}", "-pk")[: limit + 1]:
            candidates.append((getattr(row, field), source.key, row.pk, source, row))
    candidates.sort(key=lambda x: x[:3], reverse=True)
    selected = candidates[:limit]
    next_cursor = None
    if len(candidates) > limit:
        stamp, key, pk, _, _ = selected[-1]
        next_cursor = signing.dumps(
            {"binding": binding, "time": stamp.isoformat(), "source": key, "pk": pk},
            salt=CURSOR_SALT,
            compress=True,
        )
    return {
        "results": [summarize(source, row, scope) for _, _, _, source, row in selected],
        "next_cursor": next_cursor,
    }


def find_activity(scope, kind, pk):
    # POS stock is hidden only from the feed, not from authorized stock drilldowns.
    if kind == "inventory":
        qs = scope.apply(
            InventoryTransaction.objects.select_related(
                "warehouse", "owner", "product", "location", "created_by"
            )
        ).filter(posted_at__isnull=False)
        row = qs.filter(pk=pk).first()
        if row:
            return Source("inventory", "inventory", qs, "posted_at", "created_by", ("src_no",)), row
        raise NotFound()
    for source in sources(scope):
        if source.key == kind:
            row = source.qs.filter(pk=pk).first()
            if row:
                return source, row
            break
    raise NotFound("活动不存在或不在当前授权范围内。")
