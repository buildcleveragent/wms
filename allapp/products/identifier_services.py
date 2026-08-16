"""Transactional lifecycle services for product barcodes and external identifiers."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from .models import (
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductIdentifierRegistry,
    ProductPackage,
    normalize_product_identifier,
)

BARCODE_PROJECTIONS = {
    ProductBarcode.BarcodeType.GTIN: "gtin",
    ProductBarcode.BarcodeType.UNIT: "unit_barcode",
    ProductBarcode.BarcodeType.CARTON: "carton_barcode",
}
_RESERVATION_NOT_LOADED = object()


class IdentifierConcurrencyError(RuntimeError):
    pass


def _raise_concurrency_error(exc, message):
    if isinstance(exc, OperationalError) and (
        not exc.args or exc.args[0] not in (1205, 1213)
    ):
        raise exc
    raise IdentifierConcurrencyError(message) from exc


def _is_currently_effective(record, *, at=None):
    at = at or timezone.now()
    return (
        record.is_active
        and not record.is_deleted
        and (record.valid_from is None or record.valid_from <= at)
        and (record.valid_to is None or record.valid_to >= at)
    )


def _ensure_can_be_primary(record):
    if not _is_currently_effective(record):
        raise ValidationError("停用、软删除、未生效或已过期的标识不能设为主标识。")
    if isinstance(record, ProductBarcode) and record.package_id:
        package = record.package
        if package.is_deleted or not package.is_active:
            raise ValidationError("关联包装层级必须启用且未删除。")


def _semantic_keys(product, normalized_value):
    keys = set()
    if normalized_value in {
        normalize_product_identifier(product.code),
        normalize_product_identifier(product.sku),
    }:
        keys.add((None, 1))
    for record in ProductBarcode.all_objects.filter(
        product=product, normalized_value=normalized_value
    ):
        keys.add((record.package_id, record.qty_in_base))
    if ProductExternalIdentifier.all_objects.filter(
        product=product, normalized_value=normalized_value
    ).exists():
        keys.add((None, 1))
    return keys


def _validate_reservation(
    product,
    normalized_value,
    semantic_key,
    error_field,
    *,
    existing=_RESERVATION_NOT_LOADED,
):
    if existing is _RESERVATION_NOT_LOADED:
        existing = (
            ProductIdentifierRegistry.objects.filter(
                owner_id=product.owner_id,
                normalized_value=normalized_value,
            )
            .select_related("product")
            .first()
        )
    if existing and existing.product_id != product.pk:
        raise ValidationError(
            {
                error_field: f"该货主下标识“{normalized_value}”已被商品 {existing.product.code} 永久占用。"
            }
        )
    if existing:
        semantics = _semantic_keys(product, normalized_value)
        if semantics and semantic_key not in semantics:
            raise ValidationError(
                {
                    error_field: f"标识“{normalized_value}”在同一商品中具有不同包装或换算语义。"
                }
            )
    return existing


def _reserve(product, normalized_value, semantic_key, error_field="barcode"):
    existing = (
        ProductIdentifierRegistry.objects.select_for_update()
        .filter(owner_id=product.owner_id, normalized_value=normalized_value)
        .select_related("product")
        .first()
    )
    _validate_reservation(
        product,
        normalized_value,
        semantic_key,
        error_field,
        existing=existing,
    )
    if existing:
        return existing
    try:
        return ProductIdentifierRegistry.objects.create(
            owner_id=product.owner_id,
            product=product,
            normalized_value=normalized_value,
        )
    except (IntegrityError, OperationalError) as exc:
        _raise_concurrency_error(
            exc,
            f"标识“{normalized_value}”发生并发占用冲突，请刷新后重试。",
        )


def _save_record(record, *, update_fields=None):
    if update_fields is not None and "is_primary" in update_fields:
        update_fields = [*update_fields, "primary_scope"]
    record._identifier_service_write = True
    try:
        record.save(update_fields=update_fields)
    finally:
        delattr(record, "_identifier_service_write")


def _demote(queryset):
    return QuerySet.update(queryset, is_primary=False, primary_scope=None)


def _project_product(product, field, value, package=None):
    setattr(product, field, value)
    update_fields = [field, "updated_at"]
    if field == "carton_barcode":
        product.carton_package = package
        update_fields.append("carton_package")
    product._allow_identifier_projection_update = True
    try:
        product.save(update_fields=update_fields)
    finally:
        delattr(product, "_allow_identifier_projection_update")


def _project_package(package, value):
    package.barcode = value
    package._allow_identifier_projection_update = True
    try:
        package.save(update_fields=["barcode", "updated_at"])
    finally:
        delattr(package, "_allow_identifier_projection_update")


def validate_product_barcode_candidate(
    *,
    product,
    barcode,
    barcode_type,
    package=None,
    is_primary=False,
    valid_from=None,
    valid_to=None,
    is_active=True,
):
    """Validate a prospective barcode without writing identifier history."""
    barcode_type = str(barcode_type).strip().upper()
    normalized = normalize_product_identifier(barcode)
    if not normalized:
        raise ValidationError({"barcode": "条码不能为空；退役请使用 RETIRE 操作。"})

    if barcode_type in {
        ProductBarcode.BarcodeType.CARTON,
        ProductBarcode.BarcodeType.PACKAGE,
    }:
        if package is None:
            raise ValidationError({"package": "该条码类型必须指定包装层级。"})
        semantic_key = (package.pk, package.qty_in_base)
    else:
        semantic_key = (None, 1)

    record = ProductBarcode(
        owner_id=product.owner_id,
        product=product,
        barcode=str(barcode).strip(),
        normalized_value=normalized,
        barcode_type=barcode_type,
        package=package,
        qty_in_base=package.qty_in_base if package else 1,
        is_primary=is_primary,
        is_active=is_active,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    record.full_clean()
    if is_primary:
        _ensure_can_be_primary(record)
    _validate_reservation(product, normalized, semantic_key, "barcode")
    if ProductBarcode.all_objects.filter(
        product=product,
        normalized_value=normalized,
        barcode_type=barcode_type,
        package=package,
    ).exists():
        raise ValidationError(
            {"barcode": "相同商品、类型和包装层级的该条码记录已存在。"}
        )
    return record


@transaction.atomic
def add_product_barcode(
    *,
    product,
    barcode,
    barcode_type,
    package=None,
    is_primary=False,
    valid_from=None,
    valid_to=None,
    is_active=True,
    project=True,
):
    product = Product.all_objects.select_for_update().get(pk=product.pk)
    barcode_type = str(barcode_type).strip().upper()
    if barcode_type in {
        ProductBarcode.BarcodeType.CARTON,
        ProductBarcode.BarcodeType.PACKAGE,
    }:
        if package is None:
            raise ValidationError({"package": "该条码类型必须指定包装层级。"})
        package = ProductPackage.all_objects.select_for_update().get(pk=package.pk)
        if package.product_id != product.pk:
            raise ValidationError({"package": "包装层级必须属于当前商品。"})
        if package.is_deleted or not package.is_active:
            raise ValidationError({"package": "包装层级必须启用且未删除。"})
    record = validate_product_barcode_candidate(
        product=product,
        barcode=barcode,
        barcode_type=barcode_type,
        package=package,
        is_primary=is_primary,
        valid_from=valid_from,
        valid_to=valid_to,
        is_active=is_active,
    )
    semantic_key = (record.package_id, record.qty_in_base)
    _reserve(product, record.normalized_value, semantic_key)
    if is_primary:
        _demote(
            ProductBarcode.all_objects.select_for_update().filter(
                product=product,
                barcode_type=barcode_type,
                package=package,
                is_primary=True,
            )
        )
    try:
        _save_record(record)
    except (IntegrityError, OperationalError) as exc:
        _raise_concurrency_error(exc, "商品条码发生并发主码冲突，请刷新后重试。")
    if is_primary and project:
        field = BARCODE_PROJECTIONS.get(barcode_type)
        if field:
            _project_product(product, field, record.barcode, package)
        elif barcode_type == ProductBarcode.BarcodeType.PACKAGE:
            _project_package(package, record.barcode)
    return record


@transaction.atomic
def add_external_identifier(
    *,
    product,
    source_system,
    external_code,
    is_primary=False,
    valid_from=None,
    valid_to=None,
    is_active=True,
    project=True,
):
    product = Product.all_objects.select_for_update().get(pk=product.pk)
    source = normalize_product_identifier(source_system)
    normalized = normalize_product_identifier(external_code)
    if not normalized:
        raise ValidationError(
            {"external_code": "外部编码不能为空；退役请使用 RETIRE 操作。"}
        )
    _reserve(product, normalized, (None, 1), "external_code")
    if ProductExternalIdentifier.all_objects.filter(
        product=product, source_system=source, normalized_value=normalized
    ).exists():
        raise ValidationError({"external_code": "该来源系统下相同外部标识记录已存在。"})
    record = ProductExternalIdentifier(
        owner_id=product.owner_id,
        product=product,
        source_system=source,
        external_code=str(external_code).strip(),
        normalized_value=normalized,
        is_primary=is_primary,
        is_active=is_active,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    record.full_clean()
    if is_primary:
        _ensure_can_be_primary(record)
        _demote(
            ProductExternalIdentifier.all_objects.select_for_update().filter(
                product=product, source_system=source, is_primary=True
            )
        )
    try:
        _save_record(record)
    except (IntegrityError, OperationalError) as exc:
        _raise_concurrency_error(exc, "外部标识发生并发主标识冲突，请刷新后重试。")
    if is_primary and project and source == "LEGACY":
        _project_product(product, "external_code", record.external_code)
    return record


@transaction.atomic
def set_barcode_primary(record):
    record_id = record.pk
    product_id = record.product_id
    Product.all_objects.select_for_update().get(pk=product_id)
    record = (
        ProductBarcode.all_objects.select_for_update()
        .select_related("product", "package")
        .get(pk=record_id)
    )
    _ensure_can_be_primary(record)
    _demote(
        ProductBarcode.all_objects.filter(
            product=record.product,
            barcode_type=record.barcode_type,
            package=record.package,
            is_primary=True,
        ).exclude(pk=record.pk)
    )
    record.is_primary = True
    try:
        _save_record(record, update_fields=["is_primary", "updated_at"])
    except (IntegrityError, OperationalError) as exc:
        _raise_concurrency_error(exc, "商品条码发生并发主码冲突，请刷新后重试。")
    field = BARCODE_PROJECTIONS.get(record.barcode_type)
    if field:
        _project_product(record.product, field, record.barcode, record.package)
    elif record.barcode_type == ProductBarcode.BarcodeType.PACKAGE:
        _project_package(record.package, record.barcode)
    return record


@transaction.atomic
def set_external_primary(record):
    record_id = record.pk
    product_id = record.product_id
    Product.all_objects.select_for_update().get(pk=product_id)
    record = (
        ProductExternalIdentifier.all_objects.select_for_update()
        .select_related("product")
        .get(pk=record_id)
    )
    _ensure_can_be_primary(record)
    _demote(
        ProductExternalIdentifier.all_objects.filter(
            product=record.product, source_system=record.source_system, is_primary=True
        ).exclude(pk=record.pk)
    )
    record.is_primary = True
    try:
        _save_record(record, update_fields=["is_primary", "updated_at"])
    except (IntegrityError, OperationalError) as exc:
        _raise_concurrency_error(exc, "外部标识发生并发主标识冲突，请刷新后重试。")
    if record.source_system == "LEGACY":
        _project_product(record.product, "external_code", record.external_code)
    return record


@transaction.atomic
def set_identifier_active(record, active):
    record = type(record).all_objects.select_for_update().get(pk=record.pk)
    if active and record.is_deleted:
        raise ValidationError("已软删除的标识不能重新启用。")
    record.is_active = bool(active)
    _save_record(record, update_fields=["is_active", "updated_at"])
    return record


def bootstrap_product_identifiers(product):
    for value in (product.code, product.sku):
        normalized = normalize_product_identifier(value)
        if normalized:
            _reserve(product, normalized, (None, 1))
    for field, barcode_type in (
        ("gtin", ProductBarcode.BarcodeType.GTIN),
        ("unit_barcode", ProductBarcode.BarcodeType.UNIT),
        ("carton_barcode", ProductBarcode.BarcodeType.CARTON),
    ):
        value = getattr(product, field)
        if value:
            add_product_barcode(
                product=product,
                barcode=value,
                barcode_type=barcode_type,
                package=product.carton_package if field == "carton_barcode" else None,
                is_primary=True,
                project=False,
            )
    if product.external_code:
        add_external_identifier(
            product=product,
            source_system="LEGACY",
            external_code=product.external_code,
            is_primary=True,
            project=False,
        )


def bootstrap_package_identifier(package):
    if package.barcode:
        add_product_barcode(
            product=package.product,
            barcode=package.barcode,
            barcode_type=ProductBarcode.BarcodeType.PACKAGE,
            package=package,
            is_primary=True,
            project=False,
        )
