"""POS quantities grouped by basic unit identity, without cross-unit conversion."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from .models import PosSale

UNIT_FIELDS = ("product__base_uom_id", "product__base_uom__code", "product__base_uom__name")
QTY_FIELDS = ("total_qty", "sale_qty", "completed_qty", "voided_qty", "return_qty", "net_qty")


def unit_fields(product):
    """Return the basic unit shared by persisted POS quantities and their labels."""
    unit = product.base_uom if product.base_uom_id else None
    return {
        "base_unit_id": product.base_uom_id,
        "base_unit_code": unit.code if unit else "",
        "base_unit": (unit.name or unit.code) if unit else "单位未设置",
    }


def _unit_from_row(row):
    return {
        "base_unit_id": row[UNIT_FIELDS[0]],
        "base_unit_code": row[UNIT_FIELDS[1]] or "",
        "base_unit": row[UNIT_FIELDS[2]] or row[UNIT_FIELDS[1]] or "单位未设置",
    }


def _serialize(groups):
    return [
        {
            key: format(value, ".3f") if isinstance(value, Decimal) else value
            for key, value in row.items()
        }
        for _, row in sorted(groups.items(), key=lambda item: item[0] or 0)
    ]


def pos_quantities_by_unit(lines, returns, *, group_by=None):
    """Aggregate scoped facts; optionally return a mapping keyed by owner/document ID."""
    result = defaultdict(dict)
    extra = (group_by,) if group_by else ()
    sales = lines.order_by().values(*extra, *UNIT_FIELDS, "sale__status").annotate(qty=Sum("qty"))
    refunds = returns.order_by().values(*extra, *UNIT_FIELDS).annotate(qty=Sum("qty"))
    for rows, is_return in ((sales, False), (refunds, True)):
        for row in rows:
            groups = result[row[group_by] if group_by else None]
            current = groups.setdefault(
                row[UNIT_FIELDS[0]],
                {**_unit_from_row(row), **dict.fromkeys(QTY_FIELDS, Decimal("0.000"))},
            )
            qty = row["qty"] or Decimal("0")
            if is_return:
                current["return_qty"] += qty
            else:
                current["total_qty"] += qty
                if row["sale__status"] == PosSale.Status.COMPLETED:
                    current["sale_qty"] += qty
                    current["completed_qty"] += qty
                elif row["sale__status"] == PosSale.Status.VOIDED:
                    current["voided_qty"] += qty
            current["net_qty"] = current["completed_qty"] - current["return_qty"]
    if group_by:
        return {key: _serialize(groups) for key, groups in result.items()}
    return _serialize(result[None])


def document_quantities(lines):
    """Group already loaded document lines without depending on document status."""
    groups = {}
    for line in lines:
        unit = unit_fields(line.product)
        row = groups.setdefault(unit["base_unit_id"], {**unit, "qty": Decimal("0.000")})
        row["qty"] += line.qty
    return _serialize(groups)


def document_quantity_map(lines, group_by):
    """Bulk document totals for exports, with one query regardless of document count."""
    result = defaultdict(list)
    for row in lines.order_by().values(group_by, *UNIT_FIELDS).annotate(qty=Sum("qty")):
        result[row[group_by]].append({**_unit_from_row(row), "qty": format(row["qty"], ".3f")})
    return {
        key: sorted(rows, key=lambda row: row["base_unit_id"] or 0) for key, rows in result.items()
    }


def quantity_text(rows, field="qty"):
    """Format grouped quantities for human-readable exports; never invent a unit."""
    if rows is None:
        return "单位明细不可用"
    parts = []
    for row in rows:
        number = format(Decimal(row[field]), "f")
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        label = row.get("base_unit") or row.get("base_unit_code") or "单位未设置"
        parts.append(f"{number} {label}")
    return "、".join(parts) or "0"
