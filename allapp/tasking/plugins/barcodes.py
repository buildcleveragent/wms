# -*- coding: utf-8 -*-
"""
Minimal default barcode resolver for tasking.services

Dotted path (for settings):
    TASKING_BARCODE_RESOLVER = "allapp.tasking.barcodes.default_resolver"

Contract:
    def default_resolver(owner_id:int, barcode:str) -> object:
        Returns an object with attributes used by services.scan_task:
            - product_id: Optional[int]
            - code_type: str  (e.g. "SKU"/"GTIN"/"LOC"/"LPN"/"RAW")
            - label_key: str  (the raw string or normalized value)
            - uom_code: Optional[str]
            - pack_qty: Decimal (multiplier to base qty; default 1)
            - lot_no / mfg_date / exp_date (optional)

Notes:
- To avoid circular imports, we DO NOT import from services.BarcodeResolveResult.
  We simply return a lightweight object (SimpleNamespace) with the same attributes.
- This resolver is intentionally heuristic and conservative; adjust to your data rules.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime
import re

from django.db.models import Q

try:
    from allapp.products.models import Product
except Exception:  # pragma: no cover
    Product = None  # type: ignore

try:
    from allapp.locations.models import Location
except Exception:  # pragma: no cover
    Location = None  # type: ignore


_BARCODE_MULTIPLIER_RE = re.compile(r"^[^*]+\*(\d+)$")  # e.g., "SKU123*10" -> 10
_GTIN_RE = re.compile(r"^\d{13,14}$")
_DATE8_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _parse_multiplier(barcode: str) -> tuple[str, Decimal]:
    m = _BARCODE_MULTIPLIER_RE.match(barcode)
    if not m:
        return barcode, Decimal("1")
    return barcode.split("*")[0], Decimal(m.group(1))


def _fields(model) -> set[str]:
    return {f.name for f in getattr(model, "_meta").get_fields()} if model else set()


def _date_from_yyyymmdd(s: str):
    m = _DATE8_RE.match(s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    try:
        return datetime(y, mo, d).date()
    except ValueError:
        return None


def default_resolver(owner_id: int, barcode: str):
    raw = (barcode or "").strip()
    label_key, pack_qty = _parse_multiplier(raw)

    # 1) LOT/EXP syntax: "LOT:xxxx|EXP:yyyymmdd" (optional)
    lot_no = None
    exp_date = None
    if "|" in label_key or label_key.upper().startswith("LOT:"):
        parts = [p.strip() for p in label_key.split("|")]
        for p in parts:
            u = p.upper()
            if u.startswith("LOT:"):
                lot_no = p.split(":", 1)[1].strip()
            elif u.startswith("EXP:"):
                exp_date = _date_from_yyyymmdd(p.split(":", 1)[1].strip())
        # erase decorators, keep the left-most token as the item code if present
        if parts and ":" in parts[0]:
            label_key = parts[0].split(":", 1)[1].strip() or label_key

    # 2) Location prefixes (keep simple)
    if label_key.upper().startswith(("LOC:", "LOC-")) and Location:
        code = label_key.split(":", 1)[-1].split("-", 1)[-1].strip()
        # Don't query DB here — services.putaway requires location_id from API
        return SimpleNamespace(
            product_id=None,
            code_type="LOC",
            label_key=label_key,
            uom_code=None,
            pack_qty=pack_qty,
            lot_no=lot_no,
            exp_date=exp_date,
        )

    # 3) Try Product matching
    product_id = None
    code_type = "RAW"
    if Product:
        fset = _fields(Product)
        q = Q(owner_id=owner_id)
        eq_fields = [
            "code", "sku", "gtin", "barcode", "unit_barcode", "carton_barcode", "external_code",
        ]
        cond: Q | None = None
        for fname in eq_fields:
            if fname in fset:
                # case-insensitive OR exact
                part = Q(**{f"{fname}__iexact": label_key}) | Q(**{fname: label_key})
                cond = part if cond is None else (cond | part)
        if cond is not None:
            p = (
                Product.objects.filter(q & cond)
                .only("id")
                .order_by("id")
                .first()
            )
            if p:
                product_id = p.id
                # Simple guess for code_type
                if _GTIN_RE.match(label_key):
                    code_type = "GTIN"
                elif "sku" in fset:
                    code_type = "SKU"
                else:
                    code_type = "ITEM"

    return SimpleNamespace(
        product_id=product_id,
        code_type=code_type,
        label_key=label_key,
        uom_code=None,
        pack_qty=pack_qty,
        lot_no=lot_no,
        exp_date=exp_date,
    )
