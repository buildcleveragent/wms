from __future__ import annotations

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from allapp.billing.models import Bill, qmoney
from allapp.inventory.models import InventoryDetail, InventorySummary


def _available_qty_expression():
    return (
        F("onhand_qty")
        - F("allocated_qty")
        - F("locked_qty")
        - F("damaged_qty")
    )


def repair_inventory_detail_available_quantities(*, owner_id=None, warehouse_id=None):
    queryset = InventoryDetail.objects.filter(is_active=True)
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)

    expected_available = ExpressionWrapper(
        _available_qty_expression(),
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )
    mismatched_ids = list(
        queryset.annotate(expected_available=expected_available)
        .filter(expected_available__gte=0)
        .exclude(available_qty=F("expected_available"))
        .values_list("id", flat=True)
    )
    if not mismatched_ids:
        return {"processed": queryset.count(), "updated": 0}

    updated = InventoryDetail.objects.filter(id__in=mismatched_ids).update(
        available_qty=_available_qty_expression()
    )
    return {"processed": queryset.count(), "updated": updated}


def rebuild_inventory_summaries(*, owner_id=None):
    detail_queryset = InventoryDetail.objects.filter(is_active=True)
    summary_queryset = InventorySummary.objects.filter(is_active=True)
    if owner_id:
        detail_queryset = detail_queryset.filter(owner_id=owner_id)
        summary_queryset = summary_queryset.filter(owner_id=owner_id)

    owner_product_pairs = set(
        detail_queryset.values_list("owner_id", "product_id").distinct()
    )
    owner_product_pairs.update(
        summary_queryset.values_list("owner_id", "product_id").distinct()
    )

    processed = created = updated = 0
    for pair_owner_id, pair_product_id in sorted(owner_product_pairs):
        aggregates = (
            InventoryDetail.objects.filter(
                owner_id=pair_owner_id,
                product_id=pair_product_id,
                is_active=True,
            ).aggregate(
                onhand=Sum("onhand_qty"),
                allocated=Sum("allocated_qty"),
                locked=Sum("locked_qty"),
                damaged=Sum("damaged_qty"),
            )
        )

        summary = (
            InventorySummary.objects.filter(
                owner_id=pair_owner_id,
                product_id=pair_product_id,
                is_active=True,
            ).first()
        )
        was_created = summary is None
        if summary is None:
            summary = InventorySummary(
                owner_id=pair_owner_id,
                product_id=pair_product_id,
            )

        before = (
            Decimal(summary.onhand_qty or 0),
            Decimal(summary.allocated_qty or 0),
            Decimal(summary.locked_qty or 0),
            Decimal(summary.damaged_qty or 0),
            Decimal(summary.available_qty or 0),
        )
        summary.onhand_qty = Decimal(aggregates["onhand"] or 0).quantize(Decimal("0.0001"))
        summary.allocated_qty = Decimal(aggregates["allocated"] or 0).quantize(Decimal("0.0001"))
        summary.locked_qty = Decimal(aggregates["locked"] or 0).quantize(Decimal("0.0001"))
        summary.damaged_qty = Decimal(aggregates["damaged"] or 0).quantize(Decimal("0.0001"))
        summary.save()
        after = (
            Decimal(summary.onhand_qty or 0),
            Decimal(summary.allocated_qty or 0),
            Decimal(summary.locked_qty or 0),
            Decimal(summary.damaged_qty or 0),
            Decimal(summary.available_qty or 0),
        )

        processed += 1
        if was_created:
            created += 1
        elif before != after:
            updated += 1

    return {"processed": processed, "created": created, "updated": updated}


def recalculate_bill_headers(*, owner_id=None, warehouse_id=None):
    queryset = Bill.objects.prefetch_related("lines")
    if owner_id:
        queryset = queryset.filter(owner_id=owner_id)
    if warehouse_id:
        queryset = queryset.filter(warehouse_id=warehouse_id)

    processed = updated = 0
    for bill in queryset:
        processed += 1
        subtotal = qmoney(
            sum((Decimal(line.amount or 0) for line in bill.lines.all()), Decimal("0.00"))
        )
        tax_total = qmoney(
            sum((Decimal(line.tax_amount or 0) for line in bill.lines.all()), Decimal("0.00"))
        )
        total = qmoney(Decimal(subtotal or 0) + Decimal(tax_total or 0))
        if (
            qmoney(bill.subtotal) == subtotal
            and qmoney(bill.tax_total) == tax_total
            and qmoney(bill.total) == total
        ):
            continue
        Bill.objects.filter(pk=bill.pk).update(
            subtotal=subtotal,
            tax_total=tax_total,
            total=total,
        )
        updated += 1

    return {"processed": processed, "updated": updated}


def apply_safe_data_accuracy_fixes(*, owner_id=None, warehouse_id=None):
    fixes = {
        "inventory_details": repair_inventory_detail_available_quantities(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        ),
        "inventory_summaries": {
            "processed": 0,
            "created": 0,
            "updated": 0,
            "skipped": bool(warehouse_id),
            "reason": (
                "InventorySummary is owner+product scoped; summary rebuild is skipped when warehouse scope is provided."
                if warehouse_id
                else ""
            ),
        },
        "bill_headers": recalculate_bill_headers(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        ),
    }
    if not warehouse_id:
        fixes["inventory_summaries"] = {
            **rebuild_inventory_summaries(owner_id=owner_id),
            "skipped": False,
            "reason": "",
        }
    return fixes
