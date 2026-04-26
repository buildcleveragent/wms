from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.billing.enums import AccrualStatus, BillStatus
from allapp.billing.models import Bill, BillingAccrual, BillingJobRun
from allapp.inbound.models import InboundOrder
from allapp.inventory.models import InventoryDetail, InventorySummary, ReviewDifference
from allapp.locations.models import Location, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.tasking.models import WmsTask

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")


def _prefer_warehouse_scope(user) -> bool:
    return bool(getattr(user, "warehouse_id", None))


def _decimal_or_zero(value, default=ZERO_MONEY):
    return default if value is None else value


def _quantize_rate(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _percent(numerator, denominator) -> Decimal | None:
    if not denominator:
        return None
    return _quantize_rate((Decimal(numerator) / Decimal(denominator)) * Decimal("100"))


def _current_date(now: datetime.datetime | None = None):
    current = now or timezone.now()
    if timezone.is_naive(current):
        return current.date()
    return timezone.localtime(current).date()


def _inventory_used_volume_expr():
    return ExpressionWrapper(
        F("onhand_qty") * F("product__volume"),
        output_field=DecimalField(max_digits=24, decimal_places=6),
    )


def _inventory_row_used_volume(row):
    onhand_qty = getattr(row, "onhand_qty", None) or ZERO_QTY
    product_volume = getattr(getattr(row, "product", None), "volume", None) or Decimal("0")
    return onhand_qty * product_volume


def _hotspot_level(rate: Decimal | None):
    if rate is None:
        return "watch"
    if rate >= Decimal("85.00"):
        return "hot"
    if rate >= Decimal("60.00"):
        return "warm"
    if rate >= Decimal("30.00"):
        return "watch"
    return "calm"


def _inventory_summary_fallback_queryset(*, user, owner_id: int | None = None):
    qs = InventorySummary.objects.select_related("owner", "product").filter(is_active=True)
    if not user or not user.is_authenticated:
        return qs.none()
    if (
        not getattr(user, "is_superuser", False)
        and getattr(user, "owner_id", None)
        and not _prefer_warehouse_scope(user)
    ):
        qs = qs.filter(owner_id=user.owner_id)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    return qs


def _inventory_summary_aggregate(summary_qs):
    totals = {
        "onhand_qty": ZERO_QTY,
        "available_qty": ZERO_QTY,
        "locked_qty": ZERO_QTY,
        "damaged_qty": ZERO_QTY,
        "sku_count": 0,
        "owner_count": 0,
        "used_volume_m3": Decimal("0.000000"),
    }
    owner_ids = set()
    product_ids = set()

    for row in summary_qs.iterator():
        onhand_qty = row.onhand_qty or ZERO_QTY
        available_qty = row.available_qty or ZERO_QTY
        locked_qty = row.locked_qty or ZERO_QTY
        damaged_qty = row.damaged_qty or ZERO_QTY
        product_volume = getattr(getattr(row, "product", None), "volume", None) or Decimal("0")

        totals["onhand_qty"] += onhand_qty
        totals["available_qty"] += available_qty
        totals["locked_qty"] += locked_qty
        totals["damaged_qty"] += damaged_qty
        totals["used_volume_m3"] += onhand_qty * product_volume

        if row.owner_id:
            owner_ids.add(row.owner_id)
        if row.product_id:
            product_ids.add(row.product_id)

    totals["owner_count"] = len(owner_ids)
    totals["sku_count"] = len(product_ids)
    return {
        "onhand_qty": _decimal_or_zero(totals["onhand_qty"], ZERO_QTY),
        "available_qty": _decimal_or_zero(totals["available_qty"], ZERO_QTY),
        "locked_qty": _decimal_or_zero(totals["locked_qty"], ZERO_QTY),
        "damaged_qty": _decimal_or_zero(totals["damaged_qty"], ZERO_QTY),
        "sku_count": totals["sku_count"] or 0,
        "owner_count": totals["owner_count"] or 0,
        "used_volume_m3": _decimal_or_zero(totals["used_volume_m3"], Decimal("0.000000")),
    }


def _inventory_owner_rankings_from_summary(summary_qs, *, item_limit: int):
    owner_map = {}
    for row in summary_qs.iterator():
        owner_bucket = owner_map.setdefault(
            row.owner_id,
            {
                "owner": row.owner_id,
                "owner_name": getattr(getattr(row, "owner", None), "name", "") or f"Owner #{row.owner_id}",
                "onhand_qty": ZERO_QTY,
                "available_qty": ZERO_QTY,
                "locked_qty": ZERO_QTY,
                "location_count": 0,
                "used_volume_m3": Decimal("0.000000"),
                "sku_ids": set(),
            },
        )
        onhand_qty = row.onhand_qty or ZERO_QTY
        product_volume = getattr(getattr(row, "product", None), "volume", None) or Decimal("0")

        owner_bucket["onhand_qty"] += onhand_qty
        owner_bucket["available_qty"] += row.available_qty or ZERO_QTY
        owner_bucket["locked_qty"] += row.locked_qty or ZERO_QTY
        owner_bucket["used_volume_m3"] += onhand_qty * product_volume
        if row.product_id:
            owner_bucket["sku_ids"].add(row.product_id)

    rows = []
    for owner_id, owner_bucket in owner_map.items():
        rows.append(
            {
                "owner": owner_id,
                "owner_name": owner_bucket["owner_name"],
                "onhand_qty": _decimal_or_zero(owner_bucket["onhand_qty"], ZERO_QTY),
                "available_qty": _decimal_or_zero(owner_bucket["available_qty"], ZERO_QTY),
                "locked_qty": _decimal_or_zero(owner_bucket["locked_qty"], ZERO_QTY),
                "sku_count": len(owner_bucket["sku_ids"]),
                "location_count": 0,
                "used_volume_m3": _decimal_or_zero(
                    owner_bucket["used_volume_m3"],
                    Decimal("0.000000"),
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            -(row["used_volume_m3"] or Decimal("0.000000")),
            -(row["onhand_qty"] or ZERO_QTY),
            row["owner"] or 0,
        )
    )
    return rows[:item_limit]


def _inventory_owner_rankings_from_detail(inventory_qs, *, item_limit: int):
    owner_map = {}
    for row in inventory_qs.iterator():
        owner_bucket = owner_map.setdefault(
            row.owner_id,
            {
                "owner": row.owner_id,
                "owner_name": getattr(getattr(row, "owner", None), "name", "") or f"Owner #{row.owner_id}",
                "onhand_qty": ZERO_QTY,
                "available_qty": ZERO_QTY,
                "locked_qty": ZERO_QTY,
                "sku_ids": set(),
                "location_ids": set(),
                "used_volume_m3": Decimal("0.000000"),
            },
        )
        owner_bucket["onhand_qty"] += row.onhand_qty or ZERO_QTY
        owner_bucket["available_qty"] += row.available_qty or ZERO_QTY
        owner_bucket["locked_qty"] += row.locked_qty or ZERO_QTY
        owner_bucket["used_volume_m3"] += _inventory_row_used_volume(row)
        if row.product_id:
            owner_bucket["sku_ids"].add(row.product_id)
        if row.location_id:
            owner_bucket["location_ids"].add(row.location_id)

    rows = []
    for owner_id, owner_bucket in owner_map.items():
        rows.append(
            {
                "owner": owner_id,
                "owner_name": owner_bucket["owner_name"],
                "onhand_qty": _decimal_or_zero(owner_bucket["onhand_qty"], ZERO_QTY),
                "available_qty": _decimal_or_zero(owner_bucket["available_qty"], ZERO_QTY),
                "locked_qty": _decimal_or_zero(owner_bucket["locked_qty"], ZERO_QTY),
                "sku_count": len(owner_bucket["sku_ids"]),
                "location_count": len(owner_bucket["location_ids"]),
                "used_volume_m3": _decimal_or_zero(
                    owner_bucket["used_volume_m3"],
                    Decimal("0.000000"),
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            -(row["used_volume_m3"] or Decimal("0.000000")),
            -(row["onhand_qty"] or ZERO_QTY),
            row["owner"] or 0,
        )
    )
    return rows[:item_limit]


def _inventory_location_rows_from_detail(inventory_qs, *, today: datetime.date):
    location_map = {}
    for row in inventory_qs.iterator():
        location_bucket = location_map.setdefault(
            row.location_id,
            {
                "location": row.location_id,
                "location_code": getattr(getattr(row, "location", None), "code", "") or "",
                "location_name": getattr(getattr(row, "location", None), "name", "") or "",
                "subwarehouse_name": getattr(
                    getattr(getattr(row, "location", None), "subwarehouse", None),
                    "name",
                    "",
                )
                or "",
                "is_frozen": bool(getattr(getattr(row, "location", None), "is_frozen", False)),
                "capacity_volume_m3": _decimal_or_zero(
                    getattr(getattr(row, "location", None), "max_volume_m3", None),
                    Decimal("0.000"),
                ),
                "onhand_qty": ZERO_QTY,
                "available_qty": ZERO_QTY,
                "sku_ids": set(),
                "owner_ids": set(),
                "used_volume_m3": Decimal("0.000000"),
                "latest_updated_at": None,
            },
        )
        location_bucket["onhand_qty"] += row.onhand_qty or ZERO_QTY
        location_bucket["available_qty"] += row.available_qty or ZERO_QTY
        location_bucket["used_volume_m3"] += _inventory_row_used_volume(row)
        if row.product_id:
            location_bucket["sku_ids"].add(row.product_id)
        if row.owner_id:
            location_bucket["owner_ids"].add(row.owner_id)
        updated_at = getattr(row, "updated_at", None)
        if updated_at and (
            location_bucket["latest_updated_at"] is None
            or updated_at > location_bucket["latest_updated_at"]
        ):
            location_bucket["latest_updated_at"] = updated_at

    rows = []
    for location_id, location_bucket in location_map.items():
        capacity = _decimal_or_zero(location_bucket["capacity_volume_m3"], Decimal("0.000"))
        used_volume = _decimal_or_zero(location_bucket["used_volume_m3"], Decimal("0.000000"))
        utilization_rate = _percent(used_volume, capacity)
        latest_updated_at = location_bucket["latest_updated_at"]
        stale_days = max((today - latest_updated_at.date()).days, 0) if latest_updated_at else None
        rows.append(
            {
                "location": location_id,
                "location_code": location_bucket["location_code"],
                "location_name": location_bucket["location_name"],
                "subwarehouse_name": location_bucket["subwarehouse_name"],
                "is_frozen": location_bucket["is_frozen"],
                "onhand_qty": _decimal_or_zero(location_bucket["onhand_qty"], ZERO_QTY),
                "available_qty": _decimal_or_zero(location_bucket["available_qty"], ZERO_QTY),
                "sku_count": len(location_bucket["sku_ids"]),
                "owner_count": len(location_bucket["owner_ids"]),
                "used_volume_m3": used_volume,
                "capacity_volume_m3": capacity,
                "volume_utilization_rate": utilization_rate,
                "hotspot_level": _hotspot_level(utilization_rate),
                "latest_updated_at": latest_updated_at,
                "stale_days": stale_days,
            }
        )

    rows.sort(
        key=lambda row: (
            -(row["used_volume_m3"] or Decimal("0.000000")),
            -(row["onhand_qty"] or ZERO_QTY),
            row["location_code"],
        )
    )
    return rows


def scope_queryset_for_user(
    qs: QuerySet,
    user,
    *,
    owner_field: str | None = "owner_id",
    warehouse_field: str | None = "warehouse_id",
):
    if not user or not user.is_authenticated:
        return qs.none()
    if getattr(user, "is_superuser", False):
        return qs

    owner_id = getattr(user, "owner_id", None)
    warehouse_id = getattr(user, "warehouse_id", None)

    if owner_id and owner_field and not warehouse_id:
        qs = qs.filter(**{owner_field: owner_id})
    elif owner_id and owner_field is None and (warehouse_field is None or not warehouse_id):
        return qs.none()

    if warehouse_id and warehouse_field:
        qs = qs.filter(**{warehouse_field: warehouse_id})
    elif warehouse_id and warehouse_field is None:
        return qs.none()

    return qs


def _apply_scope_filter(
    qs: QuerySet,
    *,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    owner_field: str | None = "owner_id",
    warehouse_field: str | None = "warehouse_id",
):
    if owner_id:
        if owner_field is None:
            return qs.none()
        qs = qs.filter(**{owner_field: owner_id})
    if warehouse_id:
        if warehouse_field is None:
            return qs.none()
        qs = qs.filter(**{warehouse_field: warehouse_id})
    return qs


def _resolve_scope_label(model_cls, pk):
    if not pk:
        return ""
    obj = model_cls.objects.filter(pk=pk).only("name").first()
    if obj is None:
        return ""
    return getattr(obj, "name", "")


def _collect_owner_options(*querysets: QuerySet):
    owner_map = {}
    for qs in querysets:
        if qs is None:
            continue
        for row in qs.values("owner_id", "owner__name").distinct():
            owner_id = row.get("owner_id")
            if not owner_id:
                continue
            owner_map[owner_id] = {
                "id": owner_id,
                "name": row.get("owner__name") or f"Owner #{owner_id}",
            }
    return sorted(owner_map.values(), key=lambda item: (item["name"], item["id"]))


def _build_owner_options(*, user, warehouse_id: int | None = None):
    if getattr(user, "owner_id", None) and not _prefer_warehouse_scope(user):
        owner_name = getattr(getattr(user, "owner", None), "name", "") or f"Owner #{user.owner_id}"
        return [{"id": user.owner_id, "name": owner_name}]

    base_kwargs = {"warehouse_id": warehouse_id} if warehouse_id else {}
    inbound_qs = _apply_scope_filter(
        scope_queryset_for_user(InboundOrder.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    outbound_qs = _apply_scope_filter(
        scope_queryset_for_user(OutboundOrder.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    task_qs = _apply_scope_filter(
        scope_queryset_for_user(WmsTask.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    inventory_qs = _apply_scope_filter(
        scope_queryset_for_user(InventoryDetail.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    accrual_qs = _apply_scope_filter(
        scope_queryset_for_user(BillingAccrual.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    bill_qs = _apply_scope_filter(
        scope_queryset_for_user(Bill.objects.all(), user),
        warehouse_id=warehouse_id,
    )
    options = _collect_owner_options(
        inbound_qs,
        outbound_qs,
        task_qs,
        inventory_qs,
        accrual_qs,
        bill_qs,
    )
    if options:
        return options

    owner_qs = Owner.objects.all()
    if base_kwargs:
        owner_qs = owner_qs.filter(
            Q(inbound_orders__warehouse_id=warehouse_id)
            | Q(outbound_orders__warehouse_id=warehouse_id)
            | Q(tasks__warehouse_id=warehouse_id)
            | Q(inventorydetail__warehouse_id=warehouse_id)
        ).distinct()
    owner_rows = [{"id": owner.id, "name": owner.name} for owner in owner_qs.order_by("name", "id")]
    if owner_rows:
        return owner_rows

    summary_options = _collect_owner_options(_inventory_summary_fallback_queryset(user=user))
    if summary_options:
        return summary_options
    return []


def _build_scope_payload(*, user, owner_id: int | None, warehouse_id: int | None, owner_options):
    owner_name_map = {item["id"]: item["name"] for item in owner_options}
    scope_owner_id = owner_id or getattr(user, "owner_id", None)
    scope_owner_name = owner_name_map.get(scope_owner_id, "")
    if not scope_owner_name and scope_owner_id and getattr(user, "owner_id", None) == scope_owner_id:
        scope_owner_name = getattr(getattr(user, "owner", None), "name", "") or f"Owner #{scope_owner_id}"

    scope_warehouse_id = warehouse_id or getattr(user, "warehouse_id", None)
    return {
        "owner": scope_owner_id,
        "owner_name": scope_owner_name,
        "warehouse": scope_warehouse_id,
        "warehouse_name": _resolve_scope_label(Warehouse, scope_warehouse_id),
    }


def _task_progress_rows(task_qs, today: datetime.date):
    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
    labels = {
        WmsTask.TaskType.RECEIVE: "收货",
        WmsTask.TaskType.PICK: "拣货",
        WmsTask.TaskType.REVIEW: "复核",
    }
    rows = []
    for task_type in [WmsTask.TaskType.RECEIVE, WmsTask.TaskType.PICK, WmsTask.TaskType.REVIEW]:
        type_qs = task_qs.filter(task_type=task_type)
        today_total = type_qs.filter(created_at__date=today).count()
        today_completed = type_qs.filter(created_at__date=today, status=WmsTask.Status.COMPLETED).count()
        backlog = type_qs.exclude(status__in=closed_statuses).count()
        rows.append(
            {
                "task_type": task_type,
                "label": labels[task_type],
                "today_total": today_total,
                "today_completed": today_completed,
                "completion_rate": _percent(today_completed, today_total),
                "backlog": backlog,
            }
        )
    return rows


def _build_trend_payload(*, inbound_qs, outbound_qs, accrual_qs, start_date: datetime.date, end_date: datetime.date):
    inbound_map = {
        row["biz_date"]: row["count"]
        for row in (
            inbound_qs.filter(biz_date__range=(start_date, end_date))
            .values("biz_date")
            .annotate(count=Count("id"))
        )
    }
    outbound_map = {
        row["biz_date"]: row["count"]
        for row in (
            outbound_qs.filter(biz_date__range=(start_date, end_date))
            .values("biz_date")
            .annotate(count=Count("id"))
        )
    }
    accrual_map = {}
    for row in (
        accrual_qs.filter(service_date__range=(start_date, end_date))
        .values("service_date")
        .annotate(subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        accrual_map[row["service_date"]] = subtotal + tax_total

    rows = []
    cursor = start_date
    while cursor <= end_date:
        rows.append(
            {
                "date": cursor,
                "inbound_orders": inbound_map.get(cursor, 0),
                "outbound_orders": outbound_map.get(cursor, 0),
                "accrual_total": accrual_map.get(cursor, ZERO_MONEY),
            }
        )
        cursor += datetime.timedelta(days=1)
    return rows


def _build_alert_counts(*, task_qs, inventory_qs, bill_qs, job_qs, review_diff_qs, today, now):
    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
    return {
        "overdue_tasks": task_qs.exclude(status__in=closed_statuses).filter(
            planned_end__isnull=False,
            planned_end__lt=now,
        ).count(),
        "pending_review_tasks": task_qs.filter(task_type=WmsTask.TaskType.REVIEW).exclude(
            status__in=closed_statuses
        ).count(),
        "expiring_inventory": inventory_qs.filter(
            expiry_date__isnull=False,
            expiry_date__gte=today,
            expiry_date__lte=today + datetime.timedelta(days=7),
        ).count(),
        "overdue_bills": bill_qs.exclude(status__in=[BillStatus.PAID, BillStatus.VOID]).filter(
            due_date__isnull=False,
            due_date__lt=today,
        ).count(),
        "failed_billing_jobs": job_qs.filter(
            status=BillingJobRun.Status.FAILED,
            service_date__gte=today - datetime.timedelta(days=7),
        ).count(),
        "review_differences": review_diff_qs.exclude(
            status__in=[ReviewDifference.Status.COMPLETED, ReviewDifference.Status.CANCELLED]
        ).count(),
    }


def build_boss_home_payload(*, user, owner_id: int | None = None, warehouse_id: int | None = None):
    now = timezone.now()
    today = _current_date(now)
    month_start = today.replace(day=1)
    trend_start = today - datetime.timedelta(days=6)

    owner_options = _build_owner_options(user=user, warehouse_id=warehouse_id or getattr(user, "warehouse_id", None))

    inbound_qs = _apply_scope_filter(
        scope_queryset_for_user(InboundOrder.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    outbound_qs = _apply_scope_filter(
        scope_queryset_for_user(OutboundOrder.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    task_qs = _apply_scope_filter(
        scope_queryset_for_user(WmsTask.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    inventory_qs = _apply_scope_filter(
        scope_queryset_for_user(
            InventoryDetail.objects.select_related("owner", "warehouse", "product", "location"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    ).filter(onhand_qty__gt=0)
    inventory_summary_qs = _inventory_summary_fallback_queryset(user=user, owner_id=owner_id)
    accrual_qs = _apply_scope_filter(
        scope_queryset_for_user(
            BillingAccrual.objects.select_related("owner", "warehouse", "period"),
            user,
        ).filter(is_reversal=False).exclude(status=AccrualStatus.VOID),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    bill_qs = _apply_scope_filter(
        scope_queryset_for_user(
            Bill.objects.select_related("owner", "warehouse", "period"),
            user,
        ).exclude(status=BillStatus.VOID),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    job_qs = _apply_scope_filter(
        scope_queryset_for_user(BillingJobRun.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    review_diff_qs = _apply_scope_filter(
        scope_queryset_for_user(
            ReviewDifference.objects.select_related("warehouse"),
            user,
            owner_field=None,
            warehouse_field="warehouse_id",
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        owner_field=None,
        warehouse_field="warehouse_id",
    )
    location_qs = _apply_scope_filter(
        scope_queryset_for_user(
            Location.objects.filter(is_disabled=False),
            user,
            owner_field=None,
            warehouse_field="warehouse_id",
        ),
        warehouse_id=warehouse_id,
        owner_field=None,
        warehouse_field="warehouse_id",
    )

    detail_inventory_exists = inventory_qs.exists()
    if detail_inventory_exists:
        inventory_summary = inventory_qs.aggregate(
            onhand_qty=Sum("onhand_qty"),
            available_qty=Sum("available_qty"),
            locked_qty=Sum("locked_qty"),
            damaged_qty=Sum("damaged_qty"),
        )
        occupied_location_count = inventory_qs.values("location_id").distinct().count()
        used_volume_total = Decimal("0.000000")
        for row in inventory_qs.iterator():
            used_volume_total += _inventory_row_used_volume(row)
    else:
        inventory_summary = _inventory_summary_aggregate(inventory_summary_qs)
        occupied_location_count = 0
        used_volume_total = inventory_summary["used_volume_m3"]
    active_location_count = location_qs.count()
    volume_capacity_total = _decimal_or_zero(
        location_qs.aggregate(total=Sum("max_volume_m3"))["total"],
        Decimal("0.000"),
    )

    today_accrual = accrual_qs.filter(service_date=today).aggregate(
        subtotal=Sum("amount"),
        tax_total=Sum("tax_amount"),
    )
    month_billed_total = _decimal_or_zero(
        bill_qs.filter(issue_date__range=(month_start, today)).aggregate(total=Sum("total"))["total"]
    )
    overdue_receivable_total = _decimal_or_zero(
        bill_qs.exclude(status__in=[BillStatus.PAID, BillStatus.VOID]).filter(
            due_date__isnull=False,
            due_date__lt=today,
        ).aggregate(total=Sum("total"))["total"]
    )

    alert_counts = _build_alert_counts(
        task_qs=task_qs,
        inventory_qs=inventory_qs,
        bill_qs=bill_qs,
        job_qs=job_qs,
        review_diff_qs=review_diff_qs,
        today=today,
        now=now,
    )

    revenue_top_owners = []
    for row in (
        accrual_qs.filter(service_date__range=(month_start, today))
        .values("owner_id", "owner__name")
        .annotate(
            accrual_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("-subtotal", "owner_id")[:5]
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        revenue_top_owners.append(
            {
                "owner": row["owner_id"],
                "owner_name": row["owner__name"] or f"Owner #{row['owner_id']}",
                "accrual_count": row["accrual_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    if detail_inventory_exists:
        inventory_top_owners = _inventory_owner_rankings_from_detail(inventory_qs, item_limit=5)
    else:
        inventory_top_owners = _inventory_owner_rankings_from_summary(inventory_summary_qs, item_limit=5)

    attention_items = [
        {"key": "overdue_tasks", "label": "超时任务", "count": alert_counts["overdue_tasks"], "severity": "high"},
        {
            "key": "overdue_bills",
            "label": "逾期应收账单",
            "count": alert_counts["overdue_bills"],
            "severity": "high",
        },
        {
            "key": "failed_billing_jobs",
            "label": "计费作业失败",
            "count": alert_counts["failed_billing_jobs"],
            "severity": "high",
        },
        {
            "key": "pending_review_tasks",
            "label": "待复核积压",
            "count": alert_counts["pending_review_tasks"],
            "severity": "medium",
        },
        {
            "key": "expiring_inventory",
            "label": "7天内临期库存",
            "count": alert_counts["expiring_inventory"],
            "severity": "medium",
        },
        {
            "key": "review_differences",
            "label": "盘点差异待处理",
            "count": alert_counts["review_differences"],
            "severity": "medium",
        },
    ]
    attention_items = [item for item in attention_items if item["count"]]
    attention_items.sort(key=lambda item: (item["severity"] != "high", -item["count"], item["label"]))

    today_accrual_subtotal = _decimal_or_zero(today_accrual["subtotal"])
    today_accrual_tax = _decimal_or_zero(today_accrual["tax_total"])
    summary = {
        "today_inbound_orders": inbound_qs.filter(biz_date=today).count(),
        "today_outbound_orders": outbound_qs.filter(biz_date=today).count(),
        "current_onhand_qty": _decimal_or_zero(inventory_summary["onhand_qty"], ZERO_QTY),
        "current_available_qty": _decimal_or_zero(inventory_summary["available_qty"], ZERO_QTY),
        "current_locked_qty": _decimal_or_zero(inventory_summary["locked_qty"], ZERO_QTY),
        "current_damaged_qty": _decimal_or_zero(inventory_summary["damaged_qty"], ZERO_QTY),
        "occupied_location_count": occupied_location_count,
        "active_location_count": active_location_count,
        "location_occupancy_rate": _percent(occupied_location_count, active_location_count),
        "used_volume_m3": used_volume_total,
        "capacity_volume_m3": volume_capacity_total,
        "volume_utilization_rate": _percent(used_volume_total, volume_capacity_total),
        "today_accrual_total": today_accrual_subtotal + today_accrual_tax,
        "month_billed_total": month_billed_total,
        "overdue_receivable_total": overdue_receivable_total,
        "open_alert_count": sum(alert_counts.values()),
    }

    return {
        "scope": _build_scope_payload(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            owner_options=owner_options,
        ),
        "owner_options": owner_options,
        "summary": summary,
        "task_progress": _task_progress_rows(task_qs, today),
        "rankings": {
            "revenue_top_owners": revenue_top_owners,
            "inventory_top_owners": inventory_top_owners,
        },
        "trend_7d": _build_trend_payload(
            inbound_qs=inbound_qs,
            outbound_qs=outbound_qs,
            accrual_qs=accrual_qs,
            start_date=trend_start,
            end_date=today,
        ),
        "attention_items": attention_items[:5],
    }


def build_boss_alert_payload(
    *,
    user,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    item_limit: int = 8,
):
    now = timezone.now()
    today = _current_date(now)

    owner_options = _build_owner_options(user=user, warehouse_id=warehouse_id or getattr(user, "warehouse_id", None))

    task_qs = _apply_scope_filter(
        scope_queryset_for_user(WmsTask.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    inventory_qs = _apply_scope_filter(
        scope_queryset_for_user(
            InventoryDetail.objects.select_related("owner", "warehouse", "product", "location"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    ).filter(onhand_qty__gt=0)
    bill_qs = _apply_scope_filter(
        scope_queryset_for_user(
            Bill.objects.select_related("owner", "warehouse", "period"),
            user,
        ).exclude(status=BillStatus.VOID),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    job_qs = _apply_scope_filter(
        scope_queryset_for_user(BillingJobRun.objects.select_related("owner", "warehouse"), user),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    review_diff_qs = _apply_scope_filter(
        scope_queryset_for_user(
            ReviewDifference.objects.select_related("warehouse"),
            user,
            owner_field=None,
            warehouse_field="warehouse_id",
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        owner_field=None,
        warehouse_field="warehouse_id",
    )

    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]

    overdue_tasks = []
    for task in (
        task_qs.exclude(status__in=closed_statuses)
        .filter(planned_end__isnull=False, planned_end__lt=now)
        .order_by("planned_end", "id")[:item_limit]
    ):
        overdue_hours = Decimal((now - task.planned_end).total_seconds() / 3600).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )
        overdue_tasks.append(
            {
                "id": task.id,
                "task_no": task.task_no,
                "task_type": task.task_type,
                "owner": task.owner_id,
                "owner_name": getattr(task.owner, "name", ""),
                "status": task.status,
                "planned_end": task.planned_end,
                "overdue_hours": overdue_hours,
            }
        )

    pending_review_tasks = []
    for task in (
        task_qs.filter(task_type=WmsTask.TaskType.REVIEW)
        .exclude(status__in=closed_statuses)
        .order_by("created_at", "id")[:item_limit]
    ):
        pending_review_tasks.append(
            {
                "id": task.id,
                "task_no": task.task_no,
                "owner": task.owner_id,
                "owner_name": getattr(task.owner, "name", ""),
                "status": task.status,
                "created_at": task.created_at,
            }
        )

    expiring_inventory = []
    for row in (
        inventory_qs.filter(
            expiry_date__isnull=False,
            expiry_date__gte=today,
            expiry_date__lte=today + datetime.timedelta(days=7),
        )
        .order_by("expiry_date", "id")[:item_limit]
    ):
        expiring_inventory.append(
            {
                "id": row.id,
                "owner": row.owner_id,
                "owner_name": getattr(row.owner, "name", ""),
                "product": row.product_id,
                "product_name": getattr(row.product, "name", ""),
                "location": row.location_id,
                "location_code": getattr(row.location, "code", ""),
                "expiry_date": row.expiry_date,
                "onhand_qty": row.onhand_qty,
            }
        )

    overdue_bills = []
    for bill in (
        bill_qs.exclude(status__in=[BillStatus.PAID, BillStatus.VOID])
        .filter(due_date__isnull=False, due_date__lt=today)
        .order_by("due_date", "id")[:item_limit]
    ):
        overdue_bills.append(
            {
                "id": bill.id,
                "invoice_no": bill.invoice_no,
                "owner": bill.owner_id,
                "owner_name": getattr(bill.owner, "name", ""),
                "due_date": bill.due_date,
                "total": bill.total,
                "status": bill.status,
            }
        )

    failed_billing_jobs = []
    for job in (
        job_qs.filter(
            status=BillingJobRun.Status.FAILED,
            service_date__gte=today - datetime.timedelta(days=7),
        )
        .order_by("-service_date", "-id")[:item_limit]
    ):
        failed_billing_jobs.append(
            {
                "id": job.id,
                "job_name": job.job_name,
                "owner": job.owner_id,
                "owner_name": getattr(job.owner, "name", ""),
                "service_date": job.service_date,
                "message": job.message,
                "finished_at": job.finished_at,
            }
        )

    review_differences = []
    for diff in (
        review_diff_qs.exclude(status__in=[ReviewDifference.Status.COMPLETED, ReviewDifference.Status.CANCELLED])
        .order_by("created_at", "id")[:item_limit]
    ):
        review_differences.append(
            {
                "id": diff.id,
                "order_no": diff.order_no,
                "status": diff.status,
                "created_at": diff.created_at,
                "warehouse": diff.warehouse_id,
                "warehouse_name": getattr(diff.warehouse, "name", ""),
            }
        )

    sections = {
        "overdue_tasks": {
            "label": "超时任务",
            "severity": "high",
            "count": task_qs.exclude(status__in=closed_statuses).filter(
                planned_end__isnull=False,
                planned_end__lt=now,
            ).count(),
            "items": overdue_tasks,
        },
        "pending_review_tasks": {
            "label": "待复核积压",
            "severity": "medium",
            "count": task_qs.filter(task_type=WmsTask.TaskType.REVIEW).exclude(
                status__in=closed_statuses
            ).count(),
            "items": pending_review_tasks,
        },
        "expiring_inventory": {
            "label": "7天内临期库存",
            "severity": "medium",
            "count": inventory_qs.filter(
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=today + datetime.timedelta(days=7),
            ).count(),
            "items": expiring_inventory,
        },
        "overdue_bills": {
            "label": "逾期应收账单",
            "severity": "high",
            "count": bill_qs.exclude(status__in=[BillStatus.PAID, BillStatus.VOID]).filter(
                due_date__isnull=False,
                due_date__lt=today,
            ).count(),
            "items": overdue_bills,
        },
        "failed_billing_jobs": {
            "label": "计费作业失败",
            "severity": "high",
            "count": job_qs.filter(
                status=BillingJobRun.Status.FAILED,
                service_date__gte=today - datetime.timedelta(days=7),
            ).count(),
            "items": failed_billing_jobs,
        },
        "review_differences": {
            "label": "盘点差异待处理",
            "severity": "medium",
            "count": review_diff_qs.exclude(
                status__in=[ReviewDifference.Status.COMPLETED, ReviewDifference.Status.CANCELLED]
            ).count(),
            "items": review_differences,
        },
    }

    total_items = sum(section["count"] for section in sections.values())
    high_risk_items = sum(
        section["count"] for section in sections.values() if section["severity"] == "high"
    )

    return {
        "scope": _build_scope_payload(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            owner_options=owner_options,
        ),
        "owner_options": owner_options,
        "summary": {
            "section_count": len(sections),
            "total_items": total_items,
            "high_risk_items": high_risk_items,
        },
        "sections": sections,
    }


def build_boss_inventory_payload(
    *,
    user,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    item_limit: int = 8,
):
    now = timezone.now()
    today = _current_date(now)
    expiring_cutoff = today + datetime.timedelta(days=7)
    stale_cutoff = now - datetime.timedelta(days=30)

    owner_options = _build_owner_options(
        user=user,
        warehouse_id=warehouse_id or getattr(user, "warehouse_id", None),
    )
    inventory_qs = _apply_scope_filter(
        scope_queryset_for_user(
            InventoryDetail.objects.select_related(
                "owner",
                "warehouse",
                "product",
                "location",
                "location__subwarehouse",
            ),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    ).filter(onhand_qty__gt=0)
    inventory_summary_qs = _inventory_summary_fallback_queryset(user=user, owner_id=owner_id)
    location_qs = _apply_scope_filter(
        scope_queryset_for_user(
            Location.objects.filter(is_disabled=False),
            user,
            owner_field=None,
            warehouse_field="warehouse_id",
        ),
        warehouse_id=warehouse_id,
        owner_field=None,
        warehouse_field="warehouse_id",
    )

    detail_inventory_exists = inventory_qs.exists()
    if detail_inventory_exists:
        inventory_summary = inventory_qs.aggregate(
            onhand_qty=Sum("onhand_qty"),
            available_qty=Sum("available_qty"),
            locked_qty=Sum("locked_qty"),
            damaged_qty=Sum("damaged_qty"),
            sku_count=Count("product_id", distinct=True),
            owner_count=Count("owner_id", distinct=True),
        )
        occupied_location_count = inventory_qs.values("location_id").distinct().count()
        used_volume_total = Decimal("0.000000")
        for row in inventory_qs.iterator():
            used_volume_total += _inventory_row_used_volume(row)
    else:
        inventory_summary = _inventory_summary_aggregate(inventory_summary_qs)
        occupied_location_count = 0
        used_volume_total = inventory_summary["used_volume_m3"]
    active_location_count = location_qs.count()
    volume_capacity_total = _decimal_or_zero(
        location_qs.aggregate(total=Sum("max_volume_m3"))["total"],
        Decimal("0.000"),
    )

    expiring_qs = inventory_qs.filter(
        expiry_date__isnull=False,
        expiry_date__gte=today,
        expiry_date__lte=expiring_cutoff,
    )
    expiring_summary = expiring_qs.aggregate(
        onhand_qty=Sum("onhand_qty"),
        sku_count=Count("product_id", distinct=True),
    )

    stale_qs = inventory_qs.filter(updated_at__lt=stale_cutoff)
    stale_summary = stale_qs.aggregate(
        onhand_qty=Sum("onhand_qty"),
        sku_count=Count("product_id", distinct=True),
    )

    if detail_inventory_exists:
        owner_rankings = _inventory_owner_rankings_from_detail(inventory_qs, item_limit=item_limit)
    else:
        owner_rankings = _inventory_owner_rankings_from_summary(inventory_summary_qs, item_limit=item_limit)

    expiring_items = []
    for row in expiring_qs.order_by("expiry_date", "-onhand_qty", "id")[:item_limit]:
        expiring_items.append(
            {
                "id": row.id,
                "owner": row.owner_id,
                "owner_name": getattr(row.owner, "name", ""),
                "product": row.product_id,
                "product_name": getattr(row.product, "name", ""),
                "product_code": getattr(row.product, "code", ""),
                "location": row.location_id,
                "location_code": getattr(row.location, "code", ""),
                "subwarehouse_name": getattr(getattr(row.location, "subwarehouse", None), "name", ""),
                "expiry_date": row.expiry_date,
                "days_to_expiry": max((row.expiry_date - today).days, 0),
                "onhand_qty": row.onhand_qty,
                "available_qty": row.available_qty,
            }
        )

    stale_items = []
    for row in stale_qs.order_by("updated_at", "-onhand_qty", "id")[:item_limit]:
        stale_items.append(
            {
                "id": row.id,
                "owner": row.owner_id,
                "owner_name": getattr(row.owner, "name", ""),
                "product": row.product_id,
                "product_name": getattr(row.product, "name", ""),
                "product_code": getattr(row.product, "code", ""),
                "location": row.location_id,
                "location_code": getattr(row.location, "code", ""),
                "subwarehouse_name": getattr(getattr(row.location, "subwarehouse", None), "name", ""),
                "updated_at": row.updated_at,
                "stale_days": max((today - row.updated_at.date()).days, 0),
                "onhand_qty": row.onhand_qty,
                "available_qty": row.available_qty,
            }
        )

    location_rows = _inventory_location_rows_from_detail(inventory_qs, today=today)

    high_heat_locations = [
        row
        for row in location_rows
        if row["volume_utilization_rate"] is not None and row["volume_utilization_rate"] >= Decimal("60.00")
    ]
    high_heat_locations.sort(
        key=lambda row: (
            -(row["volume_utilization_rate"] or Decimal("0.00")),
            -row["used_volume_m3"],
            row["location_code"],
        )
    )

    cold_locations = [
        row
        for row in location_rows
        if row["latest_updated_at"]
        and row["latest_updated_at"] < stale_cutoff
        and (row["volume_utilization_rate"] is None or row["volume_utilization_rate"] < Decimal("30.00"))
    ]
    cold_locations.sort(
        key=lambda row: (
            -(row["stale_days"] or 0),
            -(row["onhand_qty"]),
            row["location_code"],
        )
    )

    summary = {
        "current_onhand_qty": _decimal_or_zero(inventory_summary["onhand_qty"], ZERO_QTY),
        "current_available_qty": _decimal_or_zero(inventory_summary["available_qty"], ZERO_QTY),
        "current_locked_qty": _decimal_or_zero(inventory_summary["locked_qty"], ZERO_QTY),
        "current_damaged_qty": _decimal_or_zero(inventory_summary["damaged_qty"], ZERO_QTY),
        "sku_count": inventory_summary["sku_count"] or 0,
        "owner_count": inventory_summary["owner_count"] or 0,
        "occupied_location_count": occupied_location_count,
        "active_location_count": active_location_count,
        "location_occupancy_rate": _percent(occupied_location_count, active_location_count),
        "used_volume_m3": used_volume_total,
        "capacity_volume_m3": volume_capacity_total,
        "volume_utilization_rate": _percent(used_volume_total, volume_capacity_total),
        "expiring_qty_7d": _decimal_or_zero(expiring_summary["onhand_qty"], ZERO_QTY),
        "expiring_sku_count_7d": expiring_summary["sku_count"] or 0,
        "stale_qty_30d": _decimal_or_zero(stale_summary["onhand_qty"], ZERO_QTY),
        "stale_sku_count_30d": stale_summary["sku_count"] or 0,
        "hot_location_count": len(high_heat_locations),
        "cold_location_count": len(cold_locations),
    }

    return {
        "scope": _build_scope_payload(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            owner_options=owner_options,
        ),
        "owner_options": owner_options,
        "summary": summary,
        "owner_rankings": owner_rankings,
        "expiring_items": expiring_items,
        "stale_items": stale_items,
        "high_heat_locations": high_heat_locations[:item_limit],
        "cold_locations": cold_locations[:item_limit],
        "location_hotspots": high_heat_locations[:item_limit],
    }
