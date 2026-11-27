import datetime
import importlib
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Dict, Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Exists, OuterRef, Prefetch, Sum
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.inventory.daily_close_transaction import lock_nested_close_inputs
from allapp.inventory.models import (
    InventoryDailyClose,
    InventoryDetail,
    InventoryOpeningVersion,
    InventoryHoldBalance,
    InventorySnapshotDaily,
    InventoryTransaction,
)
from allapp.locations.models import Location
from allapp.locations.models import Warehouse
from allapp.products.models import Product, ProductPackage


class MissingInventorySnapshotBaseline(ValueError):
    """There is no preceding physical snapshot for a scope with movements."""


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
    lock_nested_close_inputs(owner_id, warehouse_id)


@lru_cache(maxsize=1)
def _load_location_area_resolver():
    path = getattr(settings, "INVENTORY_SNAPSHOT_LOCATION_AREA_RESOLVER", "")
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)


def _resolve_product_unit_volume(
    product, cache: Dict[int, Optional[Decimal]]
) -> Optional[Decimal]:
    if product.id in cache:
        return cache[product.id]

    unit_volume = (
        Decimal(product.volume)
        if getattr(product, "volume", None) is not None
        else None
    )
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


def _resolve_location_area(
    location, service_date, cache: Dict[int, Optional[Decimal]]
) -> Optional[Decimal]:
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


def _row_payload_from_detail(
    detail, *, unit_volume_cache, location_area_cache, service_date
):
    unit_volume = _resolve_product_unit_volume(detail.product, unit_volume_cache)
    location_area = _resolve_location_area(
        detail.location, service_date, location_area_cache
    )
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
        "base_unit_code": _normalize_dim_text(getattr(detail, "base_unit", "")),
        "base_unit_source": InventorySnapshotDaily.UnitSource.VERIFIED,
        "unit_volume_m3_snapshot": (
            _q6(unit_volume) if unit_volume is not None else None
        ),
        "location_area_m2_snapshot": (
            _q4(location_area) if location_area is not None else None
        ),
        "snapshot_source": InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
    }


def _row_payload_from_snapshot(snapshot):
    previous_is_approximate = snapshot.snapshot_source in {
        InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
        InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
    }
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
        "base_unit_code": _normalize_dim_text(snapshot.base_unit_code),
        "base_unit_source": snapshot.base_unit_source,
        "unit_volume_m3_snapshot": snapshot.unit_volume_m3_snapshot,
        "location_area_m2_snapshot": snapshot.location_area_m2_snapshot,
        "snapshot_source": (
            InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX
            if previous_is_approximate
            else InventorySnapshotDaily.Source.TX_ROLLFORWARD
        ),
    }


def _enrich_new_row_metadata(
    row_payload,
    *,
    product_map,
    location_map,
    unit_volume_cache,
    location_area_cache,
    service_date,
):
    if not row_payload.get("base_unit_code"):
        product = product_map.get(row_payload["product_id"])
        row_payload["base_unit_code"] = _normalize_dim_text(
            getattr(getattr(product, "base_uom", None), "code", "")
        )
        row_payload["base_unit_source"] = (
            InventorySnapshotDaily.UnitSource.VERIFIED
            if row_payload["base_unit_code"]
            else InventorySnapshotDaily.UnitSource.UNKNOWN
        )
    if row_payload["unit_volume_m3_snapshot"] is None:
        product = product_map.get(row_payload["product_id"])
        if product:
            unit_volume = _resolve_product_unit_volume(product, unit_volume_cache)
            if unit_volume is not None:
                row_payload["unit_volume_m3_snapshot"] = _q6(unit_volume)

    if row_payload["location_area_m2_snapshot"] is None:
        location = location_map.get(row_payload["location_id"])
        if location:
            location_area = _resolve_location_area(
                location, service_date, location_area_cache
            )
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
        InventoryDetail.objects.filter(is_active=True)
        .select_related("product", "location")
        .prefetch_related(
            Prefetch(
                "product__packages",
                queryset=ProductPackage.objects.order_by(
                    "-is_sales_default", "-is_pickable", "sort_order", "id"
                ),
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
        payload = _row_payload_from_detail(
            detail,
            unit_volume_cache=unit_volume_cache,
            location_area_cache=location_area_cache,
            service_date=service_date,
        )
        if key not in payloads:
            payloads[key] = payload
            continue
        previous = payloads[key]
        for field in ("onhand_qty", "available_qty", "allocated_qty", "locked_qty", "damaged_qty"):
            previous[field] += payload[field]
        if previous["base_unit_code"] != payload["base_unit_code"]:
            raise ValueError("同一快照维度的库存基本单位不一致，请核对库存明细。")
    return payloads


def _opening_payload(row, owner_id, warehouse_id):
    payload = {
        field: _q4(row[field])
        for field in ("onhand_qty", "available_qty", "allocated_qty", "locked_qty", "damaged_qty")
    }
    payload.update(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        location_id=int(row["location_id"]),
        product_id=int(row["product_id"]),
        batch_no=_normalize_dim_text(row.get("batch_no")),
        serial_no=_normalize_dim_text(row.get("serial_no")),
        base_unit_code=_normalize_dim_text(row.get("base_unit_code")),
        base_unit_source=(
            InventorySnapshotDaily.UnitSource.VERIFIED
            if row.get("base_unit_code")
            else InventorySnapshotDaily.UnitSource.UNKNOWN
        ),
        snapshot_source=InventorySnapshotDaily.Source.TX_ROLLFORWARD,
        unit_volume_m3_snapshot=row.get("unit_volume_m3_snapshot"),
        location_area_m2_snapshot=row.get("location_area_m2_snapshot"),
    )
    for field in ("production_date", "expiry_date"):
        payload[field] = datetime.date.fromisoformat(row[field]) if row.get(field) else None
    return payload


def trusted_inventory_baseline(service_date, *, owner_id, warehouse_id, verify_legacy=True):
    """An approved opening supersedes the baseline for rollforward, without replacing evidence."""
    from allapp.inventory.snapshot_audit import audit_day

    scope = dict(owner_id=owner_id, warehouse_id=warehouse_id)
    header = InventoryDailyClose.objects.filter(**scope, business_date=service_date).first()
    if header:
        from allapp.inventory.daily_close import opening_is_current, posting_evidence

        approved = (
            header.opening_versions.filter(status=InventoryOpeningVersion.Status.APPROVED)
            .order_by("-version")
            .first()
        )
        if approved:
            return opening_is_current(approved, header)
        if not header.sealed_at or header.quality != header.Quality.TRUSTED or header.issues:
            return False
        manifest = header.totals.get("posting_evidence")
        if manifest and manifest != posting_evidence(service_date, scope):
            return False
        evidence = audit_day(service_date, scope, confirmed_empty=header.row_count == 0)
        return evidence["snapshot_present"] and not evidence["issues"]
    rows = InventorySnapshotDaily.objects.filter(**scope, snapshot_date=service_date)
    if (
        not rows.exists()
        or rows.exclude(snapshot_source=InventorySnapshotDaily.Source.TX_ROLLFORWARD).exists()
    ):
        return False
    if not verify_legacy:
        return True

    holds = _load_hold_balances(service_date, **scope)
    if holds is None or audit_day(service_date, scope)["issues"]:
        return False
    seen = set()
    for row in rows.iterator():
        key = _snapshot_dim_key_from_row(row)
        held = (row.allocated_qty, row.locked_qty, row.damaged_qty)
        if (
            key in seen
            or held != holds.pop(key, (0, 0, 0))
            or min(*held, row.available_qty) < 0
            or row.available_qty != row.onhand_qty - sum(held)
        ):
            return False
        seen.add(key)
    return not holds


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
    headers = _snapshot_scope_filters(
        InventoryDailyClose.objects.filter(business_date=prev_date),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    for header in headers.only(
        "id", "owner_id", "warehouse_id", "business_date", "sealed_at", "quality", "issues"
    ):
        scope = (header.owner_id, header.warehouse_id)
        opening = (
            header.opening_versions.filter(status=InventoryOpeningVersion.Status.APPROVED)
            .order_by("-version")
            .first()
        )
        from allapp.inventory.daily_close import opening_is_current

        if opening is not None and opening_is_current(opening, header):
            for key in [key for key in payloads if key[:2] == scope]:
                del payloads[key]
            for row in opening.rows:
                payload = _opening_payload(row, *scope)
                payloads[_tx_aggregate_dim_key(payload)] = payload
            scope_has_rows.add(scope)
        elif header.sealed_at and header.quality == header.Quality.TRUSTED and not header.issues:
            scope_has_rows.add(scope)
        else:
            for key, row in payloads.items():
                if key[:2] == scope:
                    row["snapshot_source"] = InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX
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
        qty_by_key[key] = qty_by_key.get(key, 0) + _q4(row["qty_delta_total"] or 0)
        scopes_with_tx.add((row["owner_id"], row["warehouse_id"]))
        product_ids.add(row["product_id"])
        location_ids.add(row["location_id"])

    products = {
        product.id: product
        for product in Product.objects.filter(id__in=product_ids)
        .select_related("base_uom")
        .prefetch_related(
            Prefetch(
                "packages",
                queryset=ProductPackage.objects.order_by(
                    "-is_sales_default", "-is_pickable", "sort_order", "id"
                ),
            )
        )
    }
    locations = {location.id: location for location in Location.objects.filter(id__in=location_ids)}
    return qty_by_key, scopes_with_tx, products, locations


def has_inventory_balance_history(service_date):
    """The cutover captured every active layer, including an empty warehouse."""
    applied = (
        MigrationRecorder.Migration.objects.filter(
            app="inventory", name="0013_inventory_hold_balance"
        )
        .values_list("applied", flat=True)
        .first()
    )
    if applied is None:
        return False
    start = (
        timezone.localdate(applied, timezone.get_default_timezone())
        if timezone.is_aware(applied)
        else applied.date()
    )
    return service_date >= start


def _load_hold_balances(service_date, *, owner_id=None, warehouse_id=None):
    if not has_inventory_balance_history(service_date):
        return None
    history = InventoryHoldBalance.objects.filter(balance_date__lte=service_date)
    later = history.filter(
        detail_id=OuterRef("detail_id"), balance_date__gt=OuterRef("balance_date")
    )
    rows = (
        _snapshot_scope_filters(history, owner_id=owner_id, warehouse_id=warehouse_id)
        .filter(~Exists(later))
        .exclude(allocated_qty=0, locked_qty=0, damaged_qty=0)
        .values(*InventoryHoldBalance.DIMENSIONS)
        .annotate(
            allocated=Sum("allocated_qty"), locked=Sum("locked_qty"), damaged=Sum("damaged_qty")
        )
    )
    balances = {}
    for row in rows.iterator():
        key = _tx_aggregate_dim_key(row)
        allocated, locked, damaged = balances.get(key, (0, 0, 0))
        balances[key] = (
            allocated + row["allocated"],
            locked + row["locked"],
            damaged + row["damaged"],
        )
    return balances


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
    sealed_headers = _snapshot_scope_filters(
        InventoryDailyClose.objects.filter(business_date=service_date, sealed_at__isnull=False),
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    sealed = set(sealed_headers.values_list("owner_id", "warehouse_id"))
    if (owner_id, warehouse_id) in sealed:
        return {
            "service_date": service_date,
            "mode": "sealed",
            "rows_created": 0,
            "scopes_processed": 0,
        }
    if sealed:
        snapshot_qs = snapshot_qs.exclude(
            Exists(
                sealed_headers.filter(
                    owner_id=OuterRef("owner_id"), warehouse_id=OuterRef("warehouse_id")
                )
            )
        )
    snapshot_qs.delete()

    if bootstrap:
        payloads = _build_bootstrap_payloads(
            service_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        payloads = {key: row for key, row in payloads.items() if key[:2] not in sealed}
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
            "scopes_processed": len(
                {(payload["owner_id"], payload["warehouse_id"]) for payload in payloads.values()}
            ),
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
    holds = _load_hold_balances(service_date, owner_id=owner_id, warehouse_id=warehouse_id)

    damaged_by_key = {}
    if holds is None:
        # Earlier snapshots have only the legacy return-quality events.
        from allapp.outbound.return_models import ReturnEvent

        quality_events = ReturnEvent.objects.filter(occurred_at__date=service_date)
        if owner_id is not None:
            quality_events = quality_events.filter(line__order__owner_id=owner_id)
        if warehouse_id is not None:
            quality_events = quality_events.filter(line__order__warehouse_id=warehouse_id)
        for event in quality_events.select_related("line").iterator(chunk_size=1000):
            dimension = dict(event.line.dimensions)
            for field in ("production_date", "expiry_date"):
                dimension[field] = (
                    datetime.date.fromisoformat(dimension[field]) if dimension[field] else None
                )
            key = _tx_aggregate_dim_key(dimension)
            damaged_by_key[key] = damaged_by_key.get(key, Decimal("0")) + event.damaged_delta

    previous_payloads = {key: row for key, row in previous_payloads.items() if key[:2] not in sealed}
    missing_baseline_scopes = sorted(tx_scopes - previous_scopes - sealed)
    if missing_baseline_scopes:
        missing = ", ".join(
            f"owner={scope[0]} warehouse={scope[1]}"
            for scope in missing_baseline_scopes
        )
        raise MissingInventorySnapshotBaseline(
            f"Missing previous inventory snapshot for {prev_date} before generating {service_date}: {missing}"
        )

    unit_volume_cache: Dict[int, Optional[Decimal]] = {}
    location_area_cache: Dict[int, Optional[Decimal]] = {}
    final_payloads = {key: dict(payload) for key, payload in previous_payloads.items()}
    approximate_scopes = {
        (previous_payload["owner_id"], previous_payload["warehouse_id"])
        for previous_payload in previous_payloads.values()
        if previous_payload["snapshot_source"]
        in {
            InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
            InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
        }
    }

    changed_keys = set(tx_by_key) | set(damaged_by_key)
    if holds is not None:
        # A reservation can start or end on a day with no physical movement.
        changed_keys.update(holds)
        changed_keys.update(
            key
            for key, row in previous_payloads.items()
            if row["allocated_qty"] or row["locked_qty"] or row["damaged_qty"]
        )
    for key in changed_keys:
        if key[:2] in sealed:
            continue
        qty_delta = tx_by_key.get(key, 0)
        payload = final_payloads.get(key)
        if payload is None:
            (
                owner_value,
                warehouse_value,
                location_value,
                product_value,
                batch_no,
                production_date,
                expiry_date,
                serial_no,
            ) = key
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
                "base_unit_code": "",
                "base_unit_source": InventorySnapshotDaily.UnitSource.UNKNOWN,
                "unit_volume_m3_snapshot": None,
                "location_area_m2_snapshot": None,
                "snapshot_source": (
                    InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX
                    if (owner_value, warehouse_value) in approximate_scopes
                    else InventorySnapshotDaily.Source.TX_ROLLFORWARD
                ),
            }
            final_payloads[key] = payload

        payload["onhand_qty"] = _q4(Decimal(payload["onhand_qty"]) + Decimal(qty_delta))
        if holds is not None:
            payload["allocated_qty"], payload["locked_qty"], payload["damaged_qty"] = holds.get(
                key, (0, 0, 0)
            )
        else:
            payload["damaged_qty"] = _q4(
                Decimal(payload["damaged_qty"]) + damaged_by_key.get(key, Decimal("0"))
            )
        available_qty = (
            Decimal(payload["onhand_qty"])
            - Decimal(payload["allocated_qty"] or 0)
            - Decimal(payload["locked_qty"] or 0)
            - Decimal(payload["damaged_qty"] or 0)
        )
        if holds is not None and available_qty < 0:
            raise ValueError(
                f"{service_date} 快照在库基线不足以覆盖已分配、锁定和破损数量，"
                f"请核对历史账面库存：{key}"
            )
        payload["available_qty"] = _q4(max(Decimal("0"), available_qty))
        if payload["snapshot_source"] != InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX:
            payload["snapshot_source"] = InventorySnapshotDaily.Source.TX_ROLLFORWARD
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
        "scopes_processed": len(
            {
                (payload["owner_id"], payload["warehouse_id"])
                for payload in final_payloads.values()
            }
        ),
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
