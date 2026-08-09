# -*- coding: utf-8 -*-
"""Deterministic owner-scoped barcode resolver used by task scanning."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from types import SimpleNamespace

from django.core.exceptions import MultipleObjectsReturned, ValidationError
from allapp.locations.models import Location
from allapp.products.identifier_lookup import get_exact_identifier_sources
from allapp.products.models import (
    ProductIdentifierRegistry,
    normalize_product_identifier,
)


_BARCODE_MULTIPLIER_RE = re.compile(r"^[^*]+\*(\d+)$")
_DATE8_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

def _parse_multiplier(barcode: str) -> tuple[str, Decimal | None]:
    """Return the lookup value and an optional explicit base-unit multiplier."""
    match = _BARCODE_MULTIPLIER_RE.match(barcode)
    if not match:
        return barcode, None
    multiplier = Decimal(match.group(1))
    if multiplier <= 0:
        raise ValidationError("条码换算数量必须大于 0。")
    return barcode.rsplit("*", 1)[0], multiplier


def _date_from_yyyymmdd(value: str):
    match = _DATE8_RE.match(value)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _result(**kwargs):
    defaults = {
        "product_id": None,
        "product_package_id": None,
        "code_type": "RAW",
        "matched_field": None,
        "matched_fields": [],
        "label_key": None,
        "uom_code": None,
        "uom_name": None,
        "pack_qty": Decimal("1"),
        "lot_no": None,
        "mfg_date": None,
        "exp_date": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def default_resolver(owner_id: int, barcode: str):
    raw = (barcode or "").strip()
    label_key, explicit_multiplier = _parse_multiplier(raw)

    lot_no = None
    exp_date = None
    if "|" in label_key or label_key.upper().startswith("LOT:"):
        parts = [part.strip() for part in label_key.split("|")]
        for part in parts:
            upper = part.upper()
            if upper.startswith("LOT:"):
                lot_no = part.split(":", 1)[1].strip()
            elif upper.startswith("EXP:"):
                exp_date = _date_from_yyyymmdd(part.split(":", 1)[1].strip())
        if parts and ":" in parts[0]:
            label_key = parts[0].split(":", 1)[1].strip() or label_key

    if label_key.upper().startswith(("LOC:", "LOC-")) and Location:
        return _result(
            code_type="LOC",
            label_key=label_key,
            pack_qty=explicit_multiplier or Decimal("1"),
            lot_no=lot_no,
            exp_date=exp_date,
        )

    normalized = normalize_product_identifier(label_key)
    try:
        sources = get_exact_identifier_sources(owner_id, normalized)
    except ProductIdentifierRegistry.DoesNotExist:
        return _result(
            label_key=label_key,
            pack_qty=explicit_multiplier or Decimal("1"),
            lot_no=lot_no,
            exp_date=exp_date,
        )
    except MultipleObjectsReturned as exc:
        raise ValidationError(f"编码冲突：标识“{normalized}”匹配到多个商品注册项。") from exc

    product = sources.registry.product
    if product.is_deleted or not product.is_active:
        raise ValidationError(f"商品标识“{normalized}”所属商品已停用或删除。")

    matched = list(sources.stable_fields)
    active_barcodes = list(sources.barcodes)
    active_external = list(sources.external_identifiers)
    type_priority = {"CARTON": 0, "PACKAGE": 1, "UNIT": 2, "GTIN": 3, "SKU": 4, "PRODUCT_CODE": 5, "EXTERNAL": 6, "OTHER": 7}
    for record in active_barcodes:
        matched.append((f"product_barcode:{record.pk}", record.barcode_type))
    for record in active_external:
        matched.append((f"external_identifier:{record.pk}", "EXTERNAL"))
    if not matched:
        if sources.has_history:
            raise ValidationError(f"条码或外部标识“{normalized}”已停用、未生效或已过期。")
        raise ValidationError(f"条码注册表异常：标识“{normalized}”未匹配商品 {product.code} 的任何标识来源。")

    semantics = {(record.package_id, record.qty_in_base) for record in active_barcodes}
    if matched and any(code_type in {"SKU", "PRODUCT_CODE", "EXTERNAL"} for _field, code_type in matched):
        semantics.add((None, 1))
    if len(semantics) > 1:
        raise ValidationError(f"编码冲突：标识“{normalized}”同时具有不同包装或换算语义。")

    matched.sort(key=lambda item: type_priority.get(item[1], 99))
    matched_fields = [field for field, _code_type in matched]
    matched_field, code_type = matched[0]
    chosen = next((record for record in active_barcodes if f"product_barcode:{record.pk}" == matched_field), None)
    package = chosen.package if chosen else None
    inferred_qty = Decimal(chosen.qty_in_base if chosen else 1)
    uom = package.uom if package else product.base_uom

    return _result(
        product_id=product.pk,
        product_package_id=package.pk if package else None,
        code_type=code_type,
        matched_field=matched_field,
        matched_fields=matched_fields,
        label_key=label_key,
        uom_code=uom.code,
        uom_name=uom.name,
        pack_qty=explicit_multiplier or inferred_qty,
        lot_no=lot_no,
        exp_date=exp_date,
    )
