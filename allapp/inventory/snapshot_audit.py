"""Read-only stock reconstruction on a single database statement snapshot."""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum, Value
from django.utils import timezone

from allapp.inventory.models import InventoryDetail, InventorySnapshotDaily, InventoryTransaction

DIMENSIONS = (
    "product_id",
    "location_id",
    "batch_no",
    "production_date",
    "expiry_date",
    "serial_no",
)


def _totals(queryset, source, quantity):
    return (
        queryset.order_by()
        .values(*DIMENSIONS)
        .annotate(record_source=Value(source), quantity=Sum(quantity))
        .values_list(*DIMENSIONS, "record_source", "quantity")
    )


def balance_evidence(day, scope):
    cutoff = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time.min)
    if settings.USE_TZ:
        cutoff = timezone.make_aware(cutoff, timezone.get_default_timezone())
    stock = InventoryDetail.objects.filter(**scope, is_active=True)
    movements = InventoryTransaction.objects.filter(**scope)
    snapshots = InventorySnapshotDaily.objects.filter(**scope, snapshot_date=day)
    # A single SQL statement gives the four aggregates one consistent read view.
    rows = _totals(snapshots, "snapshot", "onhand_qty").union(
        _totals(stock, "current", "onhand_qty"),
        _totals(movements.filter(posted_at__gte=cutoff), "later", "qty_delta"),
        _totals(movements.filter(posted_at__isnull=True), "undated", "qty_delta"),
        all=True,
    )
    balances = defaultdict(lambda: defaultdict(Decimal))
    snapshot_present = False
    for product, location, batch, produced, expiry, serial, source, qty in rows.iterator():
        key = (
            product,
            location,
            (batch or "").strip().upper(),
            produced,
            expiry,
            (serial or "").strip().upper(),
        )
        balances[key][source] += qty
        snapshot_present |= source == "snapshot"

    return balances, snapshot_present


def audit_day(day, scope, *, confirmed_empty=False):
    balances, snapshot_present = balance_evidence(day, scope)
    snapshot_present |= confirmed_empty
    issues = []
    for key, totals in balances.items():
        if "undated" in totals:
            issues.append({**dict(zip(DIMENSIONS, key)), "issue": "undated_movements"})
            continue
        reconstructed = totals["current"] - totals["later"]
        if snapshot_present and reconstructed != totals["snapshot"]:
            issues.append(
                {
                    **dict(zip(DIMENSIONS, key)),
                    "issue": "onhand_difference",
                    "snapshot_onhand": totals["snapshot"],
                    "reconstructed_onhand": reconstructed,
                }
            )
    return {
        **scope,
        "date": day,
        "snapshot_present": snapshot_present,
        "issues": issues,
    }
