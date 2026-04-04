import datetime
import importlib
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Dict, Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch, Sum

from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail, InventorySnapshotDaily, InventoryTransaction
from allapp.locations.models import Location
from allapp.locations.models import Warehouse
from allapp.products.models import Product, ProductPackage


def _q4(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _q6(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _normalize_dim_text(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _snapshot_scope_filters(queryset, owner_id=None, warehouse_id=None):
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)
    return queryset


def _lock_snapshot_scope(*, owner_id=None, warehouse_id=None):
    owner_qs = Owner.objects.order_by("id")
    warehouse_qs = Warehouse.objects.order_by("id")
    if owner_id:
        owner_qs = owner_qs.filter(id=owner_id)
    if warehouse_id:
        warehouse_qs = warehouse_qs.filter(id=warehouse_id)

    list(owner_qs.select_for_update().values_list("id", flat=True))
    list(warehouse_qs.select_for_update().values_list("id", flat=True))


@lru_cache(maxsize=1)
def _load_location_area_resolver():
    path = getattr(settings, "INVENTORY_SNAPSHOT_LOCATION_AREA_RESOLVER", "")
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)


def _resolve_product_unit_volume(product, cache: Dict[int, Optional[Decimal]]) -> Optional[Decimal]:
    if product.id in cache:
        return cache[product.id]

    unit_volume = Decimal(product.volume) if getattr(product, "volume", None) is not None else None
    if unit_volume is None:
        for pkg in product.packages.all():
            pkg_volume = getattr(pkg, "volume_m3", None)
            qty_in_base = getattr(pkg, "qty_in_base", None)
            if pkg_volume is None or not qty_in_base:
                continue
            unit_volume = Decimal(pkg_volume) / Decimal(qty_in_base)
            break

    cache[product.id] = unit_volume
    return unit_volume


def _resolve_location_area(location, service_date, cache: Dict[int, Optional[Decimal]]) -> Optional[Decimal]:
    if location.id in cache:
        return cache[location.id]

    resolver = _load_location_area_resolver()
    area_value = None
    if resolver:
        resolved = resolver(location=location, service_date=service_date)
        if resolved is not None:
            area_value = Decimal(resolved)

    cache[location.id] = area_value
    return area_value


def _snapshot_dim_key_from_values(
    *,
    owner_id,
    warehouse_id,
    location_id,
    product_id,
    batch_no,
    production_date,
    expiry_date,
    serial_no,
):
    return (
        owner_id,
        warehouse_id,
        location_id,
        product_id,
        _normalize_dim_text(batch_no),
        production_date,
        expiry_date,
        _normalize_dim_text(serial_no),
    )


def _snapshot_dim_key_from_row(row):
    return _snapshot_dim_key_from_values(
        owner_id=row.owner_id,
        warehouse_id=row.warehouse_id,
        location_id=row.location_id,
        product_id=row.product_id,
        batch_no=getattr(row, "batch_no", ""),
        production_date=getattr(row, "production_date", None),
        expiry_date=getattr(row, "expiry_date", None),
        serial_no=getattr(row, "serial_no", ""),
    )


def _tx_aggregate_dim_key(row):
    return _snapshot_dim_key_from_values(
        owner_id=row["owner_id"],
        warehouse_id=row["warehouse_id"],
        location_id=row["location_id"],
        product_id=row["product_id"],
        batch_no=row.get("batch_no"),
        production_date=row.get("production_date"),
        expiry_date=row.get("expiry_date"),
        serial_no=row.get("serial_no"),
    )


def _row_payload_from_detail(detail, *, unit_volume_cache, location_area_cache, service_date):
    unit_volume = _resolve_product_unit_volume(detail.product, unit_volume_cache)
    location_area = _resolve_location_area(detail.location, service_date, location_area_cache)
    return {
        "owner_id": detail.owner_id,
        "warehouse_id": detail.warehouse_id,
        "location_id": detail.location_id,
        "product_id": detail.product_id,
        "batch_no": _normalize_dim_text(detail.batch_no),
        "production_date": detail.production_date,
        "expiry_date": detail.expiry_date,
        "serial_no": _normalize_dim_text(detail.serial_no),
        "onhand_qty": _q4(detail.onhand_qty),
        "available_qty": _q4(detail.available_qty),
        "allocated_qty": _q4(detail.allocated_qty),
        "locked_qty": _q4(detail.locked_qty),
        "damaged_qty": _q4(detail.damaged_qty),
        "unit_volume_m3_snapshot": _q6(unit_volume) if unit_volume is not None else None,
        "location_area_m2_snapshot": _q4(location_area) if location_area is not None else None,
        "snapshot_source": "BOOTSTRAP_DETAIL",
    }


def _row_payload_from_snapshot(snapshot):
    return {
        "owner_id": snapshot.owner_id,
        "warehouse_id": snapshot.warehouse_id,
        "location_id": snapshot.location_id,
        "product_id": snapshot.product_id,
        "batch_no": _normalize_dim_text(snapshot.batch_no),
        "production_date": snapshot.production_date,
        "expiry_date": snapshot.expiry_date,
        "serial_no": _normalize_dim_text(snapshot.serial_no),
        "onhand_qty": _q4(snapshot.onhand_qty),
        "available_qty": _q4(snapshot.available_qty),
        "allocated_qty": _q4(snapshot.allocated_qty),
        "locked_qty": _q4(snapshot.locked_qty),
        "damaged_qty": _q4(snapshot.damaged_qty),
        "unit_volume_m3_snapshot": snapshot.unit_volume_m3_snapshot,
        "location_area_m2_snapshot": snapshot.location_area_m2_snapshot,
        "snapshot_source": "TX_ROLLFORWARD",
    }


def _enrich_new_row_metadata(row_payload, *, product_map, location_map, unit_volume_cache, location_area_cache, service_date):
    if row_payload["unit_volume_m3_snapshot"] is None:
        product = product_map.get(row_payload["product_id"])
        if product:
            unit_volume = _resolve_product_unit_volume(product, unit_volume_cache)
            if unit_volume is not None:
                row_payload["unit_volume_m3_snapshot"] = _q6(unit_volume)

    if row_payload["location_area_m2_snapshot"] is None:
        location = location_map.get(row_payload["location_id"])
        if location:
            location_area = _resolve_location_area(location, service_date, location_area_cache)
            if location_area is not None:
                row_payload["location_area_m2_snapshot"] = _q4(location_area)

    return row_payload


def _should_persist_snapshot_row(row_payload):
    quantities = (
        Decimal(row_payload["onhand_qty"] or 0),
        Decimal(row_payload["available_qty"] or 0),
        Decimal(row_payload["allocated_qty"] or 0),
        Decimal(row_payload["locked_qty"] or 0),
        Decimal(row_payload["damaged_qty"] or 0),
    )
    return any(value > 0 for value in quantities)


def _build_bootstrap_payloads(service_date, *, owner_id=None, warehouse_id=None):
    details = _snapshot_scope_filters(
        InventoryDetail.objects.filter(is_active=True).select_related("product", "location").prefetch_related(
            Prefetch(
                "product__packages",
                queryset=ProductPackage.objects.order_by("-is_sales_default", "-is_pickable", "sort_order", "id"),
            )
        ),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )

    unit_volume_cache: Dict[int, Optional[Decimal]] = {}
    location_area_cache: Dict[int, Optional[Decimal]] = {}
    payloads = {}
    for detail in details:
        key = _snapshot_dim_key_from_row(detail)
        payloads[key] = _row_payload_from_detail(
            detail,
            unit_volume_cache=unit_volume_cache,
            location_area_cache=location_area_cache,
            service_date=service_date,
        )
    return payloads


def _load_previous_snapshot_payloads(prev_date, *, owner_id=None, warehouse_id=None):
    previous_rows = _snapshot_scope_filters(
        InventorySnapshotDaily.objects.filter(snapshot_date=prev_date),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )

    payloads = {}
    scope_has_rows = set()
    for row in previous_rows.iterator():
        key = _snapshot_dim_key_from_row(row)
        payloads[key] = _row_payload_from_snapshot(row)
        scope_has_rows.add((row.owner_id, row.warehouse_id))
    return payloads, scope_has_rows


def _load_tx_aggregates(service_date, *, owner_id=None, warehouse_id=None):
    tx_queryset = _snapshot_scope_filters(
        InventoryTransaction.objects.filter(posted_at__date=service_date),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    tx_rows = list(
        tx_queryset.values(
            "owner_id",
            "warehouse_id",
            "location_id",
            "product_id",
            "batch_no",
            "production_date",
            "expiry_date",
            "serial_no",
        ).annotate(qty_delta_total=Sum("qty_delta"))
    )

    qty_by_key = {}
    scopes_with_tx = set()
    product_ids = set()
    location_ids = set()
    for row in tx_rows:
        key = _tx_aggregate_dim_key(row)
        qty_by_key[key] = _q4(row["qty_delta_total"] or 0)
        scopes_with_tx.add((row["owner_id"], row["warehouse_id"]))
        product_ids.add(row["product_id"])
        location_ids.add(row["location_id"])

    products = {
        product.id: product
        for product in Product.objects.filter(id__in=product_ids).prefetch_related(
            Prefetch(
                "packages",
                queryset=ProductPackage.objects.order_by("-is_sales_default", "-is_pickable", "sort_order", "id"),
            )
        )
    }
    locations = {location.id: location for location in Location.objects.filter(id__in=location_ids)}
    return qty_by_key, scopes_with_tx, products, locations


@transaction.atomic
def generate_inventory_snapshot_for_date(
    service_date: datetime.date,
    *,
    owner_id=None,
    warehouse_id=None,
    bootstrap: bool = False,
):
    _lock_snapshot_scope(owner_id=owner_id, warehouse_id=warehouse_id)
    snapshot_qs = _snapshot_scope_filters(
        InventorySnapshotDaily.objects.filter(snapshot_date=service_date),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    snapshot_qs.delete()

    if bootstrap:
        payloads = _build_bootstrap_payloads(
            service_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        rows_to_create = [
            InventorySnapshotDaily(snapshot_date=service_date, **payload)
            for payload in payloads.values()
            if _should_persist_snapshot_row(payload)
        ]
        InventorySnapshotDaily.objects.bulk_create(rows_to_create, batch_size=1000)
        return {
            "service_date": service_date,
            "mode": "bootstrap",
            "rows_created": len(rows_to_create),
            "scopes_processed": len({(payload["owner_id"], payload["warehouse_id"]) for payload in payloads.values()}),
        }

    prev_date = service_date - datetime.timedelta(days=1)
    previous_payloads, previous_scopes = _load_previous_snapshot_payloads(
        prev_date,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    tx_by_key, tx_scopes, product_map, location_map = _load_tx_aggregates(
        service_date,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )

    missing_baseline_scopes = sorted(tx_scopes - previous_scopes)
    if missing_baseline_scopes:
        missing = ", ".join(f"owner={scope[0]} warehouse={scope[1]}" for scope in missing_baseline_scopes)
        raise ValueError(
            f"Missing previous inventory snapshot for {prev_date} before generating {service_date}: {missing}"
        )

    unit_volume_cache: Dict[int, Optional[Decimal]] = {}
    location_area_cache: Dict[int, Optional[Decimal]] = {}
    final_payloads = {
        key: dict(payload)
        for key, payload in previous_payloads.items()
    }

    for key, qty_delta in tx_by_key.items():
        payload = final_payloads.get(key)
        if payload is None:
            owner_value, warehouse_value, location_value, product_value, batch_no, production_date, expiry_date, serial_no = key
            payload = {
                "owner_id": owner_value,
                "warehouse_id": warehouse_value,
                "location_id": location_value,
                "product_id": product_value,
                "batch_no": batch_no,
                "production_date": production_date,
                "expiry_date": expiry_date,
                "serial_no": serial_no,
                "onhand_qty": _q4(0),
                "available_qty": _q4(0),
                "allocated_qty": _q4(0),
                "locked_qty": _q4(0),
                "damaged_qty": _q4(0),
                "unit_volume_m3_snapshot": None,
                "location_area_m2_snapshot": None,
                "snapshot_source": "TX_ROLLFORWARD",
            }
            final_payloads[key] = payload

        payload["onhand_qty"] = _q4(Decimal(payload["onhand_qty"]) + Decimal(qty_delta))
        available_qty = (
            Decimal(payload["onhand_qty"])
            - Decimal(payload["allocated_qty"] or 0)
            - Decimal(payload["locked_qty"] or 0)
            - Decimal(payload["damaged_qty"] or 0)
        )
        payload["available_qty"] = _q4(max(Decimal("0"), available_qty))
        payload["snapshot_source"] = "TX_ROLLFORWARD"
        _enrich_new_row_metadata(
            payload,
            product_map=product_map,
            location_map=location_map,
            unit_volume_cache=unit_volume_cache,
            location_area_cache=location_area_cache,
            service_date=service_date,
        )

    rows_to_create = [
        InventorySnapshotDaily(snapshot_date=service_date, **payload)
        for payload in final_payloads.values()
        if _should_persist_snapshot_row(payload)
    ]
    InventorySnapshotDaily.objects.bulk_create(rows_to_create, batch_size=1000)
    return {
        "service_date": service_date,
        "mode": "rollforward",
        "rows_created": len(rows_to_create),
        "scopes_processed": len({(payload["owner_id"], payload["warehouse_id"]) for payload in final_payloads.values()}),
    }


def generate_inventory_snapshots_for_dates(
    service_dates: Iterable[datetime.date],
    *,
    owner_id=None,
    warehouse_id=None,
    bootstrap_first: bool = False,
):
    dates = sorted({service_date for service_date in service_dates})
    summary = {
        "service_dates": dates,
        "days": [],
        "rows_created": 0,
        "scopes_processed": 0,
    }

    for index, service_date in enumerate(dates):
        day_summary = generate_inventory_snapshot_for_date(
            service_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            bootstrap=bootstrap_first and index == 0,
        )
        summary["days"].append(day_summary)
        summary["rows_created"] += day_summary["rows_created"]
        summary["scopes_processed"] += day_summary["scopes_processed"]

    return summary
