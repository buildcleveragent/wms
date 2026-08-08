# -*- coding: utf-8 -*-
"""Deterministic owner-scoped barcode resolver used by task scanning."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from types import SimpleNamespace

from django.core.exceptions import MultipleObjectsReturned, ValidationError
from django.db.models import Q

from allapp.locations.models import Location
from allapp.products.models import (
    ProductIdentifierRegistry,
    normalize_product_identifier,
)


_BARCODE_MULTIPLIER_RE = re.compile(r"^[^*]+\*(\d+)$")
_DATE8_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

_FIELD_TYPES = (
    ("carton_barcode", "CARTON"),
    ("unit_barcode", "UNIT"),
    ("gtin", "GTIN"),
    ("sku", "SKU"),
    ("code", "PRODUCT_CODE"),
    ("external_code", "EXTERNAL"),
)


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
    registry_qs = (
        ProductIdentifierRegistry.objects.filter(
            owner_id=owner_id,
            normalized_value=normalized,
            product__is_deleted=False,
        )
        .filter(
            Q(product_package__isnull=True)
            | Q(product_package__is_deleted=False, product_package__is_active=True)
        )
        .select_related(
            "product__base_uom",
            "product__carton_package__uom",
            "product_package__uom",
        )
    )
    try:
        registry = registry_qs.get()
    except ProductIdentifierRegistry.DoesNotExist:
        return _result(
            label_key=label_key,
            pack_qty=explicit_multiplier or Decimal("1"),
            lot_no=lot_no,
            exp_date=exp_date,
        )
    except MultipleObjectsReturned as exc:
        raise ValidationError(f"编码冲突：标识“{normalized}”匹配到多个商品注册项。") from exc

    product = registry.product
    if registry.product_package_id:
        package = registry.product_package
        inferred_qty = Decimal(package.qty_in_base)
        return _result(
            product_id=product.pk,
            product_package_id=package.pk,
            code_type="PACKAGE",
            matched_field="product_package.barcode",
            matched_fields=["product_package.barcode"],
            label_key=label_key,
            uom_code=package.uom.code,
            uom_name=package.uom.name,
            pack_qty=explicit_multiplier or inferred_qty,
            lot_no=lot_no,
            exp_date=exp_date,
        )

    matched = [
        (field, code_type)
        for field, code_type in _FIELD_TYPES
        if normalize_product_identifier(getattr(product, field, None)) == normalized
    ]
    if not matched:
        raise ValidationError(
            f"条码注册表异常：标识“{normalized}”未匹配商品 {product.code} 的任何标识字段。"
        )
    matched_fields = [field for field, _code_type in matched]
    if "carton_barcode" in matched_fields and len(matched_fields) > 1:
        raise ValidationError(
            f"编码冲突：标识“{normalized}”同时命中箱码和基础单位标识 "
            f"{', '.join(matched_fields)}。"
        )

    matched_field, code_type = matched[0]
    package = None
    inferred_qty = Decimal("1")
    uom = product.base_uom
    if code_type == "CARTON":
        package = product.carton_package
        if (
            package is None
            or package.product_id != product.pk
            or package.is_deleted
            or not package.is_active
        ):
            raise ValidationError(
                f"箱码配置错误：商品 {product.code} 的箱码未绑定启用且未删除的包装层级。"
            )
        inferred_qty = Decimal(package.qty_in_base)
        uom = package.uom

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
