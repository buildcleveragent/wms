from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone


ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")


def normalize_currency(value) -> str:
    return str(value or "").strip().upper() or "UNKNOWN"


def money_groups(
    queryset,
    *,
    subtotal_field: str,
    tax_field: str,
    total_field: str | None = None,
):
    annotations = {
        "record_count": Count("id"),
        "subtotal_value": Sum(subtotal_field),
        "tax_value": Sum(tax_field),
    }
    if total_field:
        annotations["total_value"] = Sum(total_field)
    buckets = {}
    for row in queryset.values("currency").annotate(**annotations).order_by("currency"):
        currency = normalize_currency(row["currency"])
        bucket = buckets.setdefault(
            currency,
            {
                "currency": currency,
                "record_count": 0,
                "subtotal": ZERO_MONEY,
                "tax_total": ZERO_MONEY,
                "total": ZERO_MONEY,
            },
        )
        subtotal = row["subtotal_value"] or ZERO_MONEY
        tax_total = row["tax_value"] or ZERO_MONEY
        total = row.get("total_value")
        if total is None:
            total = subtotal + tax_total
        bucket["record_count"] += row["record_count"] or 0
        bucket["subtotal"] += subtotal
        bucket["tax_total"] += tax_total
        bucket["total"] += total
    return [buckets[key] for key in sorted(buckets)]


def quantity_groups(queryset, *, unit_field: str = "base_unit"):
    buckets = {}
    rows = queryset.values(unit_field).annotate(
        onhand_qty=Sum("onhand_qty"),
        available_qty=Sum("available_qty"),
        locked_qty=Sum("locked_qty"),
        damaged_qty=Sum("damaged_qty"),
        sku_count=Count("product_id", distinct=True),
    )
    for row in rows:
        unit = (row.get(unit_field) or "").strip().upper() or "UNKNOWN"
        bucket = buckets.setdefault(
            unit,
            {
                "unit_code": unit,
                "onhand_qty": ZERO_QTY,
                "available_qty": ZERO_QTY,
                "locked_qty": ZERO_QTY,
                "damaged_qty": ZERO_QTY,
                "sku_count": 0,
            },
        )
        for key in ("onhand_qty", "available_qty", "locked_qty", "damaged_qty"):
            bucket[key] += row[key] or ZERO_QTY
        bucket["sku_count"] += row["sku_count"] or 0
    return [buckets[key] for key in sorted(buckets)]


def warning(code: str, count: int, detail: str = ""):
    payload = {"code": code, "count": int(count)}
    if detail:
        payload["detail"] = detail
    return payload


def build_meta(*, scope: dict, warnings=None, unavailable: bool = False):
    warning_rows = [item for item in (warnings or []) if item.get("count", 0)]
    if unavailable:
        data_status = "UNAVAILABLE"
    elif warning_rows:
        data_status = "WARNING"
    else:
        data_status = "COMPLETE"
    canonical_scope = json.dumps(scope, sort_keys=True, ensure_ascii=True, default=str)
    return {
        "generated_at": timezone.now(),
        "data_status": data_status,
        "scope_fingerprint": hashlib.sha256(
            canonical_scope.encode("utf-8")
        ).hexdigest()[:20],
        "warnings": warning_rows,
    }


def trend_granularity(date_from, date_to) -> str:
    days = (date_to - date_from).days + 1
    if days <= 31:
        return "day"
    if days <= 180:
        return "week"
    return "month"
