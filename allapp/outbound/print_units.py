"""Display conversion only: stock, settlement and posted evidence stay in base units."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError


def _quantity_text(value):
    return format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")


def capture_print_units(product):
    return {
        "base": {"id": product.base_uom_id, "name": product.base_uom.name},
        "packages": [
            {
                "id": package.pk,
                "name": package.uom.name,
                "ratio": str(package.qty_in_base),
                "default": bool(package.is_sales_default),
            }
            for package in product.packages.filter(is_active=True, uom__is_active=True)
            .select_related("uom")
            .order_by("sort_order", "pk")
        ],
    }


def apply_print_units(lines, params):
    """Only accept the order's frozen packaging options, never a client conversion rate."""
    totals = defaultdict(Decimal)
    mode = params.get("unit_mode", "base")
    if mode not in {"base", "package"}:
        raise ValidationError("打印单位模式无效。")
    for line in lines:
        snapshot = line.print_unit_snapshot or {}
        base_unit = line.base_uom or line.product.base_uom
        base = snapshot.get("base") or {"id": base_unit.pk, "name": base_unit.name}
        packages = snapshot.get("packages", [])
        # A legacy line may provide its own reliable conversion; never use today's ratio.
        if not snapshot and line.base_uom_id and line.aux_uom_id and line.ratio and line.ratio > 0:
            packages = [
                {
                    "id": line.aux_uom_id,
                    "name": line.aux_uom.uom.name,
                    "ratio": str(line.ratio),
                    "default": True,
                }
            ]
        options = [{"value": "base", "name": base["name"], "ratio": Decimal(1)}]
        options.extend(
            {"value": str(p["id"]), "name": p["name"], "ratio": Decimal(p["ratio"])}
            for p in packages
            if Decimal(p["ratio"]) > 0
        )
        default = "base"
        if mode == "package" and packages:
            preferred = next((p for p in packages if p.get("default")), packages[0])
            default = str(preferred["id"])
        selected = params.get(f"unit_{line.pk}", default)
        option = next((o for o in options if o["value"] == selected), None)
        if option is None:
            raise ValidationError(f"第 {line.line_no} 行所选打印单位不在订单换算快照中。")
        for item in options:
            item["selected"] = item["value"] == selected
        line.print_options = options
        line.print_unit_name = option["name"]
        line.print_qty = line.base_qty / option["ratio"]
        line.print_qty_text = _quantity_text(line.print_qty)
        line.print_price = line.base_price * option["ratio"] if line.is_priced else None
        if line.print_price is not None:
            whole, _, fraction = format(line.print_price, "f").partition(".")
            line.print_price_text = whole + "." + fraction.rstrip("0").ljust(2, "0")
        else:
            line.print_price_text = ""
        line.print_ratio = option["ratio"]
        totals[option["name"]] += line.print_qty
    return [{"unit": unit, "qty": qty, "text": _quantity_text(qty)} for unit, qty in totals.items()]
