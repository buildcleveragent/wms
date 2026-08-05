from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, QuerySet, Sum
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.billing.enums import AccrualStatus, BillStatus, PricingStatus, SourceQuality
from allapp.billing.models import (
    Bill,
    BillingAccrual,
    BillingEvent,
    BillingJobRun,
    BillingMetricDaily,
)
from allapp.billing.services.ledger import financial_ledger_accruals
from allapp.inbound.models import InboundOrder
from allapp.inventory.models import (
    InventoryDetail,
    InventorySnapshotDaily,
    InventorySummary,
    ReviewDifference,
)
from allapp.locations.models import Location, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.products.models import ProductUom
from allapp.tasking.models import WmsTask

from .services_operations import (
    OperationFilters,
    build_operations_detail_rows,
    build_operations_summary,
)
from .boss_contract import (
    build_meta,
    money_groups,
    normalize_currency,
    quantity_groups,
    trend_granularity,
    warning,
)

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")


def _prefer_warehouse_scope(user) -> bool:
    return bool(AccessScope.for_user(user).warehouse_ids)


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


def _date_cutoff(date_to: datetime.date, now=None):
    current = now or timezone.now()
    if date_to == _current_date(current):
        return current
    cutoff = datetime.datetime.combine(
        date_to + datetime.timedelta(days=1), datetime.time.min
    )
    if timezone.is_aware(current):
        cutoff = timezone.make_aware(cutoff, timezone.get_current_timezone())
    return cutoff


def _inventory_used_volume_expr():
    return ExpressionWrapper(
        F("onhand_qty") * F("product__volume"),
        output_field=DecimalField(max_digits=24, decimal_places=6),
    )


def _inventory_row_used_volume(row):
    onhand_qty = getattr(row, "onhand_qty", None) or ZERO_QTY
    product_volume = getattr(row, "unit_volume_m3_snapshot", None)
    if product_volume is None:
        product_volume = getattr(getattr(row, "product", None), "volume", None)
    product_volume = product_volume or Decimal("0")
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
    qs = InventorySummary.objects.select_related("owner", "product").filter(
        is_active=True
    )
    qs = AccessScope.for_user(user).filter_queryset(
        qs, owner_field="owner_id", warehouse_field=None
    )
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    return qs


def _inventory_as_of_queryset(
    *, user, owner_id: int | None, warehouse_id: int | None, as_of: datetime.date
):
    today = _current_date()
    if as_of == today:
        qs = _apply_scope_filter(
            scope_queryset_for_user(
                InventoryDetail.objects.select_related(
                    "owner", "warehouse", "product", "product__base_uom", "location"
                ),
                user,
            ),
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        ).filter(onhand_qty__gt=0)
        return qs, "CURRENT", False, []

    qs = _apply_scope_filter(
        scope_queryset_for_user(
            InventorySnapshotDaily.objects.select_related(
                "owner", "warehouse", "product", "product__base_uom", "location"
            ),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    ).filter(snapshot_date=as_of, onhand_qty__gt=0)
    if not qs.exists():
        return (
            qs,
            "SNAPSHOT",
            True,
            [
                warning(
                    "HISTORICAL_INVENTORY_MISSING",
                    1,
                    f"{as_of.isoformat()} 没有可用库存快照。",
                )
            ],
        )
    approximate_count = qs.filter(
        snapshot_source__in=[
            InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
            InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
        ]
    ).count()
    missing_unit_count = qs.filter(base_unit_code="").count()
    inferred_unit_count = qs.filter(
        base_unit_source=InventorySnapshotDaily.UnitSource.LEGACY_INFERRED
    ).count()
    warnings = []
    if approximate_count:
        warnings.append(warning("HISTORICAL_INVENTORY_APPROXIMATE", approximate_count))
    if missing_unit_count:
        warnings.append(
            warning("HISTORICAL_INVENTORY_UNIT_UNKNOWN", missing_unit_count)
        )
    if inferred_unit_count:
        warnings.append(
            warning("HISTORICAL_INVENTORY_UNIT_INFERRED", inferred_unit_count)
        )
    return qs, "SNAPSHOT", False, warnings


def _quantity_groups_for_inventory(queryset, source_kind: str):
    if source_kind != "CURRENT":
        queryset = queryset.filter(
            base_unit_source=InventorySnapshotDaily.UnitSource.VERIFIED
        )
    groups = quantity_groups(
        queryset,
        unit_field="base_unit" if source_kind == "CURRENT" else "base_unit_code",
    )
    uom_rows = {
        row.code.upper(): row
        for row in ProductUom.objects.filter(
            code__in=[group["unit_code"] for group in groups]
        )
    }
    for group in groups:
        uom = uom_rows.get(group["unit_code"])
        group["unit_name"] = uom.name if uom else group["unit_code"]
        group["unit_type"] = uom.kind if uom else "UNKNOWN"
    return groups


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
        product_volume = getattr(
            getattr(row, "product", None), "volume", None
        ) or Decimal("0")

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
        "used_volume_m3": _decimal_or_zero(
            totals["used_volume_m3"], Decimal("0.000000")
        ),
    }


def _inventory_owner_rankings_from_summary(summary_qs, *, item_limit: int):
    owner_map = {}
    for row in summary_qs.iterator():
        owner_bucket = owner_map.setdefault(
            row.owner_id,
            {
                "owner": row.owner_id,
                "owner_name": getattr(getattr(row, "owner", None), "name", "")
                or f"Owner #{row.owner_id}",
                "onhand_qty": ZERO_QTY,
                "available_qty": ZERO_QTY,
                "locked_qty": ZERO_QTY,
                "location_count": 0,
                "used_volume_m3": Decimal("0.000000"),
                "sku_ids": set(),
            },
        )
        onhand_qty = row.onhand_qty or ZERO_QTY
        product_volume = getattr(
            getattr(row, "product", None), "volume", None
        ) or Decimal("0")

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
            -row["sku_count"],
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
                "owner_name": getattr(getattr(row, "owner", None), "name", "")
                or f"Owner #{row.owner_id}",
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
            -row["sku_count"],
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
                "location_code": getattr(getattr(row, "location", None), "code", "")
                or "",
                "location_name": getattr(getattr(row, "location", None), "name", "")
                or "",
                "subwarehouse_name": getattr(
                    getattr(getattr(row, "location", None), "subwarehouse", None),
                    "name",
                    "",
                )
                or "",
                "is_frozen": bool(
                    getattr(getattr(row, "location", None), "is_frozen", False)
                ),
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
        capacity = _decimal_or_zero(
            location_bucket["capacity_volume_m3"], Decimal("0.000")
        )
        used_volume = _decimal_or_zero(
            location_bucket["used_volume_m3"], Decimal("0.000000")
        )
        utilization_rate = _percent(used_volume, capacity)
        latest_updated_at = location_bucket["latest_updated_at"]
        stale_days = (
            max((today - latest_updated_at.date()).days, 0)
            if latest_updated_at
            else None
        )
        rows.append(
            {
                "location": location_id,
                "location_code": location_bucket["location_code"],
                "location_name": location_bucket["location_name"],
                "subwarehouse_name": location_bucket["subwarehouse_name"],
                "is_frozen": location_bucket["is_frozen"],
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
            -row["sku_count"],
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
    return AccessScope.for_user(user).filter_queryset(
        qs,
        owner_field=owner_field,
        warehouse_field=warehouse_field,
    )


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
    scope = AccessScope.for_user(user)
    if scope.warehouse_ids or scope.is_global:
        bindings = OwnerWarehouseBinding.objects.select_related("owner").filter(
            is_active=True,
            is_deleted=False,
            owner__is_active=True,
            owner__is_deleted=False,
        )
        if warehouse_id:
            bindings = bindings.filter(warehouse_id=warehouse_id)
        elif scope.warehouse_ids:
            bindings = bindings.filter(warehouse_id__in=scope.warehouse_ids)
        rows = {
            binding.owner_id: {
                "id": binding.owner_id,
                "name": binding.owner.name,
            }
            for binding in bindings.order_by("owner__name", "owner_id")
        }
        return list(rows.values())
    # Filter option lists with the same tenant boundary as the dashboard data.
    # In particular, an empty fact table is not permission to fall back to the
    # global Owner table: that used to expose every tenant to a multi-warehouse
    # boss before any activity had been recorded in the selected warehouses.
    if not scope.is_valid:
        return []
    if scope.owner_ids:
        return list(
            Owner.objects.filter(id__in=scope.owner_ids)
            .values("id", "name")
            .order_by("name", "id")
        )

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
    if scope.is_global:
        allowed_warehouse_ids = {warehouse_id} if warehouse_id else None
    elif scope.warehouse_ids:
        if warehouse_id and warehouse_id not in scope.warehouse_ids:
            return []
        allowed_warehouse_ids = (
            {warehouse_id} if warehouse_id else set(scope.warehouse_ids)
        )
    else:
        return []
    if allowed_warehouse_ids:
        owner_qs = owner_qs.filter(
            Q(inbound_orders__warehouse_id__in=allowed_warehouse_ids)
            | Q(outbound_orders__warehouse_id__in=allowed_warehouse_ids)
            | Q(tasks__warehouse_id__in=allowed_warehouse_ids)
            | Q(inventorydetail__warehouse_id__in=allowed_warehouse_ids)
        ).distinct()
    owner_rows = [
        {"id": owner.id, "name": owner.name}
        for owner in owner_qs.order_by("name", "id")
    ]
    if owner_rows:
        return owner_rows

    summary_options = _collect_owner_options(
        _inventory_summary_fallback_queryset(user=user)
    )
    if summary_options:
        return summary_options
    return []


def build_boss_context_payload(*, user, warehouse_id: int | None = None):
    today = _current_date()
    access_scope = AccessScope.for_user(user)
    warehouse_qs = Warehouse.objects.filter(is_active=True).order_by("name", "id")
    if not access_scope.is_global:
        warehouse_qs = warehouse_qs.filter(pk__in=access_scope.warehouse_ids)
    warehouse_options = [
        {"id": row.id, "code": row.code, "name": row.name} for row in warehouse_qs
    ]
    owner_options = _build_owner_options(user=user, warehouse_id=warehouse_id)
    scope = _build_scope_payload(
        user=user,
        owner_id=None,
        warehouse_id=warehouse_id,
        owner_options=owner_options,
        date_from=today.replace(day=1),
        date_to=today,
    )
    return {
        "scope": scope,
        "warehouse_options": warehouse_options,
        "owner_options": owner_options,
        "defaults": {
            "date_from": today.replace(day=1),
            "date_to": today,
        },
        "limits": {"max_range_days": 367},
        "meta": build_meta(scope=scope),
    }


def _build_scope_payload(
    *,
    user,
    owner_id: int | None,
    warehouse_id: int | None,
    owner_options,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
):
    access_scope = AccessScope.for_user(user)
    owner_name_map = {item["id"]: item["name"] for item in owner_options}
    scope_owner_id = owner_id
    scope_owner_name = owner_name_map.get(scope_owner_id, "")
    if (
        not scope_owner_name
        and scope_owner_id
        and (access_scope.is_global or scope_owner_id in access_scope.owner_ids)
    ):
        scope_owner_name = _resolve_scope_label(Owner, scope_owner_id)

    scope_warehouse_id = warehouse_id
    return {
        "mode": "WAREHOUSE" if scope_warehouse_id else "ALL_AUTHORIZED",
        "owner": scope_owner_id,
        "owner_name": scope_owner_name,
        "warehouse": scope_warehouse_id,
        "warehouse_name": (
            _resolve_scope_label(Warehouse, scope_warehouse_id)
            if scope_warehouse_id
            else "全部授权仓库"
        ),
        "date_from": date_from,
        "date_to": date_to,
    }


def _task_progress_rows(task_qs, today: datetime.date):
    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
    labels = {
        WmsTask.TaskType.RECEIVE: "收货",
        WmsTask.TaskType.PICK: "拣货",
        WmsTask.TaskType.REVIEW: "复核",
    }
    rows = []
    for task_type in [
        WmsTask.TaskType.RECEIVE,
        WmsTask.TaskType.PICK,
        WmsTask.TaskType.REVIEW,
    ]:
        type_qs = task_qs.filter(task_type=task_type)
        today_total = type_qs.filter(created_at__date=today).count()
        today_completed = type_qs.filter(
            created_at__date=today, status=WmsTask.Status.COMPLETED
        ).count()
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


def _build_trend_payload(
    *,
    inbound_qs,
    outbound_qs,
    accrual_qs,
    start_date: datetime.date,
    end_date: datetime.date,
):
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
    accrual_map = defaultdict(dict)
    for row in (
        accrual_qs.filter(service_date__range=(start_date, end_date))
        .values("service_date", "currency")
        .annotate(subtotal=Sum("amount"), tax_total=Sum("tax_amount"))
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        currency = normalize_currency(row["currency"])
        accrual_map[row["service_date"]][currency] = (
            accrual_map[row["service_date"]].get(currency, ZERO_MONEY)
            + subtotal
            + tax_total
        )

    rows = []
    cursor = start_date
    while cursor <= end_date:
        rows.append(
            {
                "date": cursor,
                "inbound_orders": inbound_map.get(cursor, 0),
                "outbound_orders": outbound_map.get(cursor, 0),
                "accruals_by_currency": [
                    {"currency": currency, "total": total}
                    for currency, total in sorted(accrual_map.get(cursor, {}).items())
                ],
            }
        )
        cursor += datetime.timedelta(days=1)
    return rows


def _aggregate_trend_rows(rows, granularity):
    if granularity == "day":
        return rows
    buckets = {}
    for row in rows:
        day = row["date"]
        bucket_date = (
            day - datetime.timedelta(days=day.weekday())
            if granularity == "week"
            else day.replace(day=1)
        )
        bucket = buckets.setdefault(
            bucket_date,
            {
                "date": bucket_date,
                "inbound_orders": 0,
                "outbound_orders": 0,
                "inbound_qty": Decimal("0"),
                "outbound_qty": Decimal("0"),
                "metric_basis": "actual",
                "currency_totals": defaultdict(lambda: ZERO_MONEY),
            },
        )
        bucket["inbound_orders"] += row.get("inbound_orders", 0)
        bucket["outbound_orders"] += row.get("outbound_orders", 0)
        bucket["inbound_qty"] += Decimal(str(row.get("inbound_qty") or 0))
        bucket["outbound_qty"] += Decimal(str(row.get("outbound_qty") or 0))
        for money_row in row.get("accruals_by_currency", []):
            bucket["currency_totals"][money_row["currency"]] += Decimal(
                str(money_row.get("total") or 0)
            )
    result = []
    for bucket_date in sorted(buckets):
        bucket = buckets[bucket_date]
        currency_totals = bucket.pop("currency_totals")
        bucket["accruals_by_currency"] = [
            {"currency": currency, "total": total}
            for currency, total in sorted(currency_totals.items())
        ]
        result.append(bucket)
    return result


def _build_alert_counts(
    *, task_qs, inventory_qs, bill_qs, job_qs, review_qs, date_from, today, now
):
    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
    cutoff = _date_cutoff(today, now)
    return {
        "overdue_tasks": task_qs.exclude(status__in=closed_statuses)
        .filter(
            planned_end__isnull=False,
            planned_end__lt=cutoff,
        )
        .count(),
        "pending_review_tasks": task_qs.filter(
            task_type=WmsTask.TaskType.REVIEW,
            created_at__date__lte=today,
        )
        .exclude(status__in=closed_statuses)
        .count(),
        "expiring_inventory": inventory_qs.filter(
            expiry_date__isnull=False,
            expiry_date__gte=today,
            expiry_date__lte=today + datetime.timedelta(days=7),
        ).count(),
        "overdue_bills": bill_qs.filter(
            status=BillStatus.ISSUED,
            due_date__isnull=False,
            due_date__lt=today,
            issue_date__lte=today,
        ).count(),
        "bills_missing_due_date": bill_qs.filter(
            status__in=[BillStatus.ISSUED, BillStatus.PAID],
            due_date__isnull=True,
            issue_date__lte=today,
        ).count(),
        "failed_billing_jobs": job_qs.filter(
            status=BillingJobRun.Status.FAILED,
            service_date__range=(date_from, today),
        ).count(),
        "review_differences": review_qs.filter(
            status__in=[
                ReviewDifference.Status.PENDING,
                ReviewDifference.Status.IN_PROGRESS,
            ],
            created_at__date__lte=today,
        ).count(),
    }


def build_boss_home_payload(
    *,
    user,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
):
    now = timezone.now()
    today = _current_date(now)
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    trend_start = date_from

    resolved_scope = AccessScope.for_user(user)
    default_warehouse_id = (
        next(iter(resolved_scope.warehouse_ids))
        if len(resolved_scope.warehouse_ids) == 1
        else None
    )
    owner_options = _build_owner_options(
        user=user, warehouse_id=warehouse_id or default_warehouse_id
    )

    today_operations = build_operations_summary(
        user=user,
        filters=OperationFilters(
            start_date=date_from,
            end_date=date_to,
            direction="all",
            metric_basis="actual",
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        ),
    )
    trend_filters = OperationFilters(
        start_date=trend_start,
        end_date=date_to,
        direction="all",
        metric_basis="actual",
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    trend_operations = build_operations_summary(user=user, filters=trend_filters)
    trend_details = build_operations_detail_rows(user=user, filters=trend_filters)
    daily_order_keys = defaultdict(lambda: {"inbound": set(), "outbound": set()})
    for item in trend_details:
        event_at = item.get("event_at") or ""
        day = event_at[:10]
        if not day:
            continue
        order_key = item.get("order_id") or item.get("task_id")
        if order_key:
            daily_order_keys[day][item["direction"]].add(order_key)

    inbound_qs = _apply_scope_filter(
        scope_queryset_for_user(
            InboundOrder.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    outbound_qs = _apply_scope_filter(
        scope_queryset_for_user(
            OutboundOrder.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    task_qs = _apply_scope_filter(
        scope_queryset_for_user(
            WmsTask.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    inventory_qs, inventory_source, inventory_unavailable, inventory_warnings = (
        _inventory_as_of_queryset(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            as_of=date_to,
        )
    )
    scoped_accrual_qs = _apply_scope_filter(
        scope_queryset_for_user(
            BillingAccrual.objects.select_related("owner", "warehouse", "period"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    accrual_qs = scoped_accrual_qs.filter(is_reversal=False).exclude(
        status=AccrualStatus.VOID
    )
    ledger_accrual_qs = financial_ledger_accruals(scoped_accrual_qs)
    bill_qs = _apply_scope_filter(
        scope_queryset_for_user(
            Bill.objects.select_related("owner", "warehouse", "period"),
            user,
        ).exclude(status=BillStatus.VOID),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    job_qs = _apply_scope_filter(
        scope_queryset_for_user(
            BillingJobRun.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    review_qs = _apply_scope_filter(
        scope_queryset_for_user(
            ReviewDifference.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
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
        occupied_location_count = inventory_qs.values("location_id").distinct().count()
        used_volume_total = Decimal("0.000000")
        for row in inventory_qs.iterator():
            used_volume_total += _inventory_row_used_volume(row)
    else:
        occupied_location_count = 0
        used_volume_total = Decimal("0.000000")
    active_location_count = location_qs.count()
    volume_capacity_total = _decimal_or_zero(
        location_qs.aggregate(total=Sum("max_volume_m3"))["total"],
        Decimal("0.000"),
    )

    range_accrual_qs = ledger_accrual_qs.filter(
        service_date__range=(date_from, date_to)
    )
    issued_bill_qs = bill_qs.filter(
        status__in=[BillStatus.ISSUED, BillStatus.PAID],
        issue_date__range=(date_from, date_to),
    )
    draft_bill_qs = bill_qs.filter(
        status=BillStatus.DRAFT,
        issue_date__range=(date_from, date_to),
    )
    overdue_bill_qs = bill_qs.filter(
        status=BillStatus.ISSUED,
        due_date__isnull=False,
        due_date__lt=date_to,
        issue_date__lte=date_to,
    )
    accruals_by_currency = money_groups(
        range_accrual_qs, subtotal_field="amount", tax_field="tax_amount"
    )
    issued_bills_by_currency = money_groups(
        issued_bill_qs,
        subtotal_field="subtotal",
        tax_field="tax_total",
        total_field="total",
    )
    draft_bills_by_currency = money_groups(
        draft_bill_qs,
        subtotal_field="subtotal",
        tax_field="tax_total",
        total_field="total",
    )
    overdue_receivables_by_currency = money_groups(
        overdue_bill_qs,
        subtotal_field="subtotal",
        tax_field="tax_total",
        total_field="total",
    )

    alert_counts = _build_alert_counts(
        task_qs=task_qs,
        inventory_qs=inventory_qs,
        bill_qs=bill_qs,
        job_qs=job_qs,
        review_qs=review_qs,
        date_from=date_from,
        today=date_to,
        now=now,
    )

    revenue_accrual_counts = {
        (row["owner_id"], row["currency"]): row["accrual_count"]
        for row in (
            accrual_qs.filter(service_date__range=(date_from, date_to))
            .values("owner_id", "currency")
            .annotate(accrual_count=Count("id"))
        )
    }
    revenue_groups = defaultdict(list)
    for row in (
        ledger_accrual_qs.filter(service_date__range=(date_from, date_to))
        .values("owner_id", "owner__name", "currency")
        .annotate(
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("currency", "-subtotal", "owner_id")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        currency = normalize_currency(row["currency"])
        if len(revenue_groups[currency]) >= 5:
            continue
        revenue_groups[currency].append(
            {
                "owner": row["owner_id"],
                "owner_name": row["owner__name"] or f"Owner #{row['owner_id']}",
                "currency": currency,
                "accrual_count": revenue_accrual_counts.get(
                    (row["owner_id"], row["currency"]), 0
                ),
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )
    revenue_contribution_by_currency = [
        {"currency": currency, "rows": rows}
        for currency, rows in sorted(revenue_groups.items())
    ]

    if detail_inventory_exists:
        inventory_top_owners = _inventory_owner_rankings_from_detail(
            inventory_qs, item_limit=5
        )
    else:
        inventory_top_owners = []

    attention_items = [
        {
            "key": "overdue_tasks",
            "label": "超时任务",
            "count": alert_counts["overdue_tasks"],
            "severity": "high",
        },
        {
            "key": "overdue_bills",
            "label": "逾期应收账单",
            "count": alert_counts["overdue_bills"],
            "severity": "high",
        },
        {
            "key": "bills_missing_due_date",
            "label": "已开票账单缺少到期日",
            "count": alert_counts["bills_missing_due_date"],
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
    attention_items.sort(
        key=lambda item: (item["severity"] != "high", -item["count"], item["label"])
    )

    quantity_by_uom = _quantity_groups_for_inventory(inventory_qs, inventory_source)
    summary = {
        "metric_basis": "actual",
        "data_as_of": today_operations["data_as_of"],
        "today_inbound_orders": today_operations["summary"]["inbound"]["orders"],
        "today_inbound_qty": today_operations["summary"]["inbound"]["qty"],
        "today_outbound_orders": today_operations["summary"]["outbound"]["orders"],
        "today_outbound_qty": today_operations["summary"]["outbound"]["qty"],
        "sku_count": inventory_qs.values("product_id").distinct().count(),
        "owner_count": inventory_qs.values("owner_id").distinct().count(),
        "occupied_location_count": occupied_location_count,
        "active_location_count": active_location_count,
        "location_occupancy_rate": _percent(
            occupied_location_count, active_location_count
        ),
        "used_volume_m3": used_volume_total,
        "capacity_volume_m3": volume_capacity_total,
        "volume_utilization_rate": _percent(used_volume_total, volume_capacity_total),
        "inventory_source": inventory_source,
        "inventory_as_of": date_to,
        "quantity_by_uom": quantity_by_uom,
        "accruals_by_currency": accruals_by_currency,
        "issued_bills_by_currency": issued_bills_by_currency,
        "draft_bills_by_currency": draft_bills_by_currency,
        "overdue_receivables_by_currency": overdue_receivables_by_currency,
        "open_alert_count": sum(alert_counts.values()),
    }

    trend_rows = _build_trend_payload(
        inbound_qs=inbound_qs,
        outbound_qs=outbound_qs,
        accrual_qs=ledger_accrual_qs,
        start_date=trend_start,
        end_date=date_to,
    )
    actual_trend = {row["date"]: row for row in trend_operations["trend"]}
    for row in trend_rows:
        day_key = row["date"].isoformat()
        operation_row = actual_trend.get(day_key, {})
        row["inbound_orders"] = len(daily_order_keys[day_key]["inbound"])
        row["outbound_orders"] = len(daily_order_keys[day_key]["outbound"])
        row["inbound_qty"] = operation_row.get("inbound_qty", "0")
        row["outbound_qty"] = operation_row.get("outbound_qty", "0")
        row["metric_basis"] = "actual"

    granularity = trend_granularity(date_from, date_to)
    trend_rows = _aggregate_trend_rows(trend_rows, granularity)

    payload = {
        "scope": _build_scope_payload(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            owner_options=owner_options,
            date_from=date_from,
            date_to=date_to,
        ),
        "owner_options": owner_options,
        "summary": summary,
        "task_progress": _task_progress_rows(task_qs, date_to),
        "rankings": {
            "revenue_contribution_by_currency": revenue_contribution_by_currency,
            "inventory_top_owners": inventory_top_owners,
        },
        "trend_7d": trend_rows,
        "granularity": granularity,
        "attention_items": attention_items[:5],
    }
    unknown_currency_count = (
        range_accrual_qs.filter(Q(currency__isnull=True) | Q(currency="")).count()
        + bill_qs.filter(Q(currency__isnull=True) | Q(currency="")).count()
    )
    if unknown_currency_count:
        inventory_warnings.append(warning("UNKNOWN_CURRENCY", unknown_currency_count))
    payload["meta"] = build_meta(
        scope=payload["scope"],
        warnings=inventory_warnings,
        unavailable=inventory_unavailable,
    )
    return payload


def build_boss_alert_payload(
    *,
    user,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    item_limit: int = 8,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
):
    now = timezone.now()
    today = _current_date(now)
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    cutoff = _date_cutoff(date_to, now)

    access_scope = AccessScope.for_user(user)
    default_warehouse_id = (
        next(iter(access_scope.warehouse_ids))
        if len(access_scope.warehouse_ids) == 1
        else None
    )
    owner_options = _build_owner_options(
        user=user, warehouse_id=warehouse_id or default_warehouse_id
    )

    task_qs = _apply_scope_filter(
        scope_queryset_for_user(
            WmsTask.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    inventory_qs, inventory_source, inventory_unavailable, inventory_warnings = (
        _inventory_as_of_queryset(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            as_of=date_to,
        )
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
        scope_queryset_for_user(
            BillingJobRun.objects.select_related("owner", "warehouse"), user
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    event_qs = _apply_scope_filter(
        scope_queryset_for_user(
            BillingEvent.objects.select_related("owner", "warehouse"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    approximate_accrual_qs = (
        _apply_scope_filter(
            scope_queryset_for_user(
                BillingAccrual.objects.select_related("owner", "warehouse", "rule"),
                user,
            ),
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        .filter(source_quality=SourceQuality.APPROXIMATE)
        .exclude(status=AccrualStatus.VOID)
    )
    approximate_metric_qs = _apply_scope_filter(
        scope_queryset_for_user(
            BillingMetricDaily.objects.select_related("owner", "warehouse"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    ).filter(source_quality=SourceQuality.APPROXIMATE)
    review_qs = _apply_scope_filter(
        scope_queryset_for_user(
            ReviewDifference.objects.select_related(
                "owner", "warehouse", "source_task", "source_task_line"
            ).prefetch_related("lines__product", "lines__location"),
            user,
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    closed_statuses = [WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]

    overdue_tasks = []
    for task in (
        task_qs.exclude(status__in=closed_statuses)
        .filter(planned_end__isnull=False, planned_end__lt=cutoff)
        .order_by("planned_end", "id")[:item_limit]
    ):
        overdue_hours = Decimal(
            (now - task.planned_end).total_seconds() / 3600
        ).quantize(
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
        task_qs.filter(
            task_type=WmsTask.TaskType.REVIEW,
            created_at__date__lte=date_to,
        )
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
    for row in inventory_qs.filter(
        expiry_date__isnull=False,
        expiry_date__gte=date_to,
        expiry_date__lte=date_to + datetime.timedelta(days=7),
    ).order_by("expiry_date", "id")[:item_limit]:
        expiring_inventory.append(
            {
                "id": row.id,
                "item_type": (
                    "inventory_detail"
                    if inventory_source == "CURRENT"
                    else "inventory_snapshot"
                ),
                "owner": row.owner_id,
                "owner_name": getattr(row.owner, "name", ""),
                "product": row.product_id,
                "product_name": getattr(row.product, "name", ""),
                "location": row.location_id,
                "location_code": getattr(row.location, "code", ""),
                "expiry_date": row.expiry_date,
                "onhand_qty": row.onhand_qty,
                "base_unit": (
                    row.base_unit
                    if inventory_source == "CURRENT"
                    else row.base_unit_code
                ),
            }
        )

    overdue_bills = []
    for bill in bill_qs.filter(
        status=BillStatus.ISSUED,
        due_date__isnull=False,
        due_date__lt=date_to,
        issue_date__lte=date_to,
    ).order_by("due_date", "id")[:item_limit]:
        overdue_bills.append(
            {
                "id": bill.id,
                "invoice_no": bill.invoice_no,
                "owner": bill.owner_id,
                "owner_name": getattr(bill.owner, "name", ""),
                "due_date": bill.due_date,
                "total": bill.total,
                "currency": normalize_currency(bill.currency),
                "status": bill.status,
            }
        )

    failed_billing_jobs = []
    for job in job_qs.filter(
        status=BillingJobRun.Status.FAILED,
        service_date__range=(date_from, date_to),
    ).order_by("-service_date", "-id")[:item_limit]:
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
    for diff in review_qs.filter(
        status__in=[
            ReviewDifference.Status.PENDING,
            ReviewDifference.Status.IN_PROGRESS,
        ],
        created_at__date__lte=date_to,
    ).order_by("created_at", "id")[:item_limit]:
        review_differences.append(
            {
                "id": diff.id,
                "order_no": diff.order_no,
                "status": diff.status,
                "created_at": diff.created_at,
                "owner": diff.owner_id,
                "owner_name": getattr(diff.owner, "name", ""),
                "warehouse": diff.warehouse_id,
                "warehouse_name": getattr(diff.warehouse, "name", ""),
                "legacy_owner_unknown": diff.owner_id is None,
            }
        )

    bills_missing_due_date = [
        {
            "id": bill.id,
            "invoice_no": bill.invoice_no,
            "owner": bill.owner_id,
            "owner_name": getattr(bill.owner, "name", ""),
            "warehouse": bill.warehouse_id,
            "warehouse_name": getattr(bill.warehouse, "name", ""),
            "issue_date": bill.issue_date,
            "status": bill.status,
            "currency": normalize_currency(bill.currency),
            "total": bill.total,
        }
        for bill in bill_qs.filter(
            status__in=[BillStatus.ISSUED, BillStatus.PAID],
            due_date__isnull=True,
            issue_date__lte=date_to,
        ).order_by("issue_date", "id")[:item_limit]
    ]

    unpriced_billing_events = [
        {
            "id": event.id,
            "owner": event.owner_id,
            "owner_name": getattr(event.owner, "name", ""),
            "warehouse": event.warehouse_id,
            "warehouse_name": getattr(event.warehouse, "name", ""),
            "service_date": event.service_date,
            "charge_type": event.charge_type,
            "calc_method": event.calc_method,
            "reason": event.pricing_reason,
            "pricing_status": event.pricing_status,
        }
        for event in event_qs.filter(
            pricing_status__in=[PricingStatus.PENDING, PricingStatus.UNPRICED],
            service_date__range=(date_from, date_to),
        ).order_by("service_date", "id")[:item_limit]
    ]
    approximate_billing_data = [
        {
            "id": accrual.id,
            "owner": accrual.owner_id,
            "owner_name": getattr(accrual.owner, "name", ""),
            "warehouse": accrual.warehouse_id,
            "warehouse_name": getattr(accrual.warehouse, "name", ""),
            "service_date": accrual.service_date,
            "charge_type": accrual.charge_type,
            "calc_method": getattr(accrual.rule, "calc_method", ""),
            "reason": accrual.source_note or "APPROXIMATE_SOURCE",
            "item_type": "accrual",
        }
        for accrual in approximate_accrual_qs.filter(
            service_date__range=(date_from, date_to)
        ).order_by("service_date", "id")[:item_limit]
    ]
    remaining = max(0, item_limit - len(approximate_billing_data))
    if remaining:
        approximate_billing_data.extend(
            {
                "id": metric.id,
                "owner": metric.owner_id,
                "owner_name": getattr(metric.owner, "name", ""),
                "warehouse": metric.warehouse_id,
                "warehouse_name": getattr(metric.warehouse, "name", ""),
                "service_date": metric.service_date,
                "charge_type": "STORAGE",
                "calc_method": metric.metric_type,
                "reason": metric.source,
                "item_type": "metric",
            }
            for metric in approximate_metric_qs.filter(
                service_date__range=(date_from, date_to)
            ).order_by("service_date", "id")[:remaining]
        )

    sections = {
        "overdue_tasks": {
            "label": "超时任务",
            "severity": "high",
            "count": task_qs.exclude(status__in=closed_statuses)
            .filter(
                planned_end__isnull=False,
                planned_end__lt=cutoff,
            )
            .count(),
            "items": overdue_tasks,
            "date_semantics": "当前未解决，计划截止日早于所选截止日",
        },
        "pending_review_tasks": {
            "label": "待复核积压",
            "severity": "medium",
            "count": task_qs.filter(
                task_type=WmsTask.TaskType.REVIEW,
                created_at__date__lte=date_to,
            )
            .exclude(status__in=closed_statuses)
            .count(),
            "items": pending_review_tasks,
            "date_semantics": "当前未解决，创建日不晚于所选截止日",
        },
        "expiring_inventory": {
            "label": "7天内临期库存",
            "severity": "medium",
            "count": inventory_qs.filter(
                expiry_date__isnull=False,
                expiry_date__gte=date_to,
                expiry_date__lte=date_to + datetime.timedelta(days=7),
            ).count(),
            "items": expiring_inventory,
            "date_semantics": "所选截止日库存中未来 7 天到期",
        },
        "overdue_bills": {
            "label": "逾期应收账单",
            "severity": "high",
            "count": bill_qs.filter(
                status=BillStatus.ISSUED,
                due_date__isnull=False,
                due_date__lt=date_to,
                issue_date__lte=date_to,
            ).count(),
            "items": overdue_bills,
            "date_semantics": "当前未付款且到期日早于所选截止日",
        },
        "bills_missing_due_date": {
            "label": "已开票账单缺少到期日",
            "severity": "high",
            "count": bill_qs.filter(
                status__in=[BillStatus.ISSUED, BillStatus.PAID],
                due_date__isnull=True,
                issue_date__lte=date_to,
            ).count(),
            "items": bills_missing_due_date,
            "date_semantics": "所选截止日前开票且当前仍缺少到期日",
        },
        "failed_billing_jobs": {
            "label": "计费作业失败",
            "severity": "high",
            "count": job_qs.filter(
                status=BillingJobRun.Status.FAILED,
                service_date__range=(date_from, date_to),
            ).count(),
            "items": failed_billing_jobs,
            "date_semantics": "服务日期位于所选区间",
        },
        "review_differences": {
            "label": "盘点差异待处理",
            "severity": "medium",
            "count": review_qs.filter(
                status__in=[
                    ReviewDifference.Status.PENDING,
                    ReviewDifference.Status.IN_PROGRESS,
                ],
                created_at__date__lte=date_to,
            ).count(),
            "items": review_differences,
            "date_semantics": "当前未解决，创建日不晚于所选截止日",
        },
        "unpriced_billing_events": {
            "label": "未定价计费事件",
            "severity": "high",
            "count": event_qs.filter(
                pricing_status__in=[PricingStatus.PENDING, PricingStatus.UNPRICED],
                service_date__range=(date_from, date_to),
            ).count(),
            "items": unpriced_billing_events,
            "date_semantics": "服务日期位于所选区间",
        },
        "approximate_billing_data": {
            "label": "近似来源计费数据",
            "severity": "high",
            "count": approximate_accrual_qs.filter(
                service_date__range=(date_from, date_to)
            ).count()
            + approximate_metric_qs.filter(
                service_date__range=(date_from, date_to)
            ).count(),
            "items": approximate_billing_data,
            "date_semantics": "服务日期位于所选区间",
        },
    }

    for section in sections.values():
        section["shown_count"] = len(section["items"])
        section["has_more"] = section["shown_count"] < section["count"]

    total_items = sum(section["count"] for section in sections.values())
    high_risk_items = sum(
        section["count"]
        for section in sections.values()
        if section["severity"] == "high"
    )

    scope_payload = _build_scope_payload(
        user=user,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        owner_options=owner_options,
        date_from=date_from,
        date_to=date_to,
    )
    data_warnings = list(inventory_warnings)
    legacy_difference_count = review_qs.filter(owner__isnull=True).count()
    if legacy_difference_count:
        data_warnings.append(
            warning("LEGACY_REVIEW_OWNER_UNKNOWN", legacy_difference_count)
        )
    unknown_currency_count = bill_qs.filter(
        Q(currency__isnull=True) | Q(currency="")
    ).count()
    if unknown_currency_count:
        data_warnings.append(warning("UNKNOWN_CURRENCY", unknown_currency_count))
    payload = {
        "scope": scope_payload,
        "owner_options": owner_options,
        "summary": {
            "section_count": len(sections),
            "total_items": total_items,
            "high_risk_items": high_risk_items,
        },
        "sections": sections,
    }
    payload["meta"] = build_meta(
        scope=scope_payload,
        warnings=data_warnings,
        unavailable=inventory_unavailable,
    )
    return payload


def build_boss_inventory_payload(
    *,
    user,
    owner_id: int | None = None,
    warehouse_id: int | None = None,
    item_limit: int = 8,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
):
    now = timezone.now()
    today = _current_date(now)
    date_to = date_to or today
    date_from = date_from or date_to.replace(day=1)
    expiring_cutoff = date_to + datetime.timedelta(days=7)
    stale_cutoff = now - datetime.timedelta(days=30)

    access_scope = AccessScope.for_user(user)
    default_warehouse_id = (
        next(iter(access_scope.warehouse_ids))
        if len(access_scope.warehouse_ids) == 1
        else None
    )
    owner_options = _build_owner_options(
        user=user,
        warehouse_id=warehouse_id or default_warehouse_id,
    )
    inventory_qs, inventory_source, inventory_unavailable, inventory_warnings = (
        _inventory_as_of_queryset(
            user=user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            as_of=date_to,
        )
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
            sku_count=Count("product_id", distinct=True),
            owner_count=Count("owner_id", distinct=True),
        )
        occupied_location_count = inventory_qs.values("location_id").distinct().count()
        used_volume_total = Decimal("0.000000")
        for row in inventory_qs.iterator():
            used_volume_total += _inventory_row_used_volume(row)
    else:
        inventory_summary = {
            "onhand_qty": ZERO_QTY,
            "available_qty": ZERO_QTY,
            "locked_qty": ZERO_QTY,
            "damaged_qty": ZERO_QTY,
            "sku_count": 0,
            "owner_count": 0,
            "used_volume_m3": Decimal("0.000000"),
        }
        occupied_location_count = 0
        used_volume_total = Decimal("0.000000")
    active_location_count = location_qs.count()
    volume_capacity_total = _decimal_or_zero(
        location_qs.aggregate(total=Sum("max_volume_m3"))["total"],
        Decimal("0.000"),
    )

    expiring_qs = inventory_qs.filter(
        expiry_date__isnull=False,
        expiry_date__gte=date_to,
        expiry_date__lte=expiring_cutoff,
    )
    expiring_summary = expiring_qs.aggregate(
        onhand_qty=Sum("onhand_qty"),
        sku_count=Count("product_id", distinct=True),
    )

    stale_qs = (
        inventory_qs.filter(updated_at__lt=stale_cutoff)
        if inventory_source == "CURRENT"
        else inventory_qs.none()
    )
    stale_summary = stale_qs.aggregate(
        onhand_qty=Sum("onhand_qty"),
        sku_count=Count("product_id", distinct=True),
    )

    if detail_inventory_exists:
        owner_rankings = _inventory_owner_rankings_from_detail(
            inventory_qs, item_limit=item_limit
        )
    else:
        owner_rankings = []

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
                "subwarehouse_name": getattr(
                    getattr(row.location, "subwarehouse", None), "name", ""
                ),
                "expiry_date": row.expiry_date,
                "days_to_expiry": max((row.expiry_date - date_to).days, 0),
                "onhand_qty": row.onhand_qty,
                "available_qty": row.available_qty,
                "base_unit": (
                    row.base_unit
                    if inventory_source == "CURRENT"
                    else row.base_unit_code
                ),
            }
        )

    stale_items = []
    stale_rows = (
        stale_qs.order_by("updated_at", "-onhand_qty", "id")[:item_limit]
        if inventory_source == "CURRENT"
        else []
    )
    for row in stale_rows:
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
                "subwarehouse_name": getattr(
                    getattr(row.location, "subwarehouse", None), "name", ""
                ),
                "updated_at": row.updated_at,
                "stale_days": max((today - row.updated_at.date()).days, 0),
                "onhand_qty": row.onhand_qty,
                "available_qty": row.available_qty,
                "base_unit": row.base_unit,
            }
        )

    location_rows = _inventory_location_rows_from_detail(inventory_qs, today=today)

    high_heat_locations = [
        row
        for row in location_rows
        if row["volume_utilization_rate"] is not None
        and row["volume_utilization_rate"] >= Decimal("60.00")
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
        and (
            row["volume_utilization_rate"] is None
            or row["volume_utilization_rate"] < Decimal("30.00")
        )
    ]
    cold_locations.sort(
        key=lambda row: (
            -(row["stale_days"] or 0),
            -row["sku_count"],
            row["location_code"],
        )
    )

    quantity_by_uom = _quantity_groups_for_inventory(inventory_qs, inventory_source)
    summary = {
        "inventory_source": inventory_source,
        "inventory_as_of": date_to,
        "quantity_by_uom": quantity_by_uom,
        "sku_count": inventory_summary["sku_count"] or 0,
        "owner_count": inventory_summary["owner_count"] or 0,
        "occupied_location_count": occupied_location_count,
        "active_location_count": active_location_count,
        "location_occupancy_rate": _percent(
            occupied_location_count, active_location_count
        ),
        "used_volume_m3": used_volume_total,
        "capacity_volume_m3": volume_capacity_total,
        "volume_utilization_rate": _percent(used_volume_total, volume_capacity_total),
        "expiring_sku_count_7d": expiring_summary["sku_count"] or 0,
        "stale_sku_count_30d": stale_summary["sku_count"] or 0,
        "hot_location_count": len(high_heat_locations),
        "cold_location_count": len(cold_locations),
    }

    scope_payload = _build_scope_payload(
        user=user,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        owner_options=owner_options,
        date_from=date_from,
        date_to=date_to,
    )
    payload = {
        "scope": scope_payload,
        "owner_options": owner_options,
        "summary": summary,
        "owner_rankings": owner_rankings,
        "expiring_items": expiring_items,
        "stale_items": stale_items,
        "high_heat_locations": high_heat_locations[:item_limit],
        "cold_locations": cold_locations[:item_limit],
        "location_hotspots": high_heat_locations[:item_limit],
    }
    if inventory_source != "CURRENT":
        inventory_warnings.append(
            warning(
                "HISTORICAL_STALE_METRIC_UNAVAILABLE",
                1,
                "历史快照不包含最后变更时间，呆滞库存指标不可用。",
            )
        )
    payload["meta"] = build_meta(
        scope=scope_payload,
        warnings=inventory_warnings,
        unavailable=inventory_unavailable,
    )
    return payload
