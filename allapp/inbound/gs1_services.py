"""Owner-scoped GS1 lookup and one-step product creation for no-order receiving."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner
from allapp.baseinfo.owner_warehouse_access import owner_ids_for_warehouses
from allapp.products.gs1 import equivalent_gtins
from allapp.products.models import (
    Brand,
    Gs1LookupCache,
    Product,
    ProductCategory,
    ProductIdentifierRegistry,
    ProductUom,
    format_owner_sequence_identifier,
)


def require_quick_create_owner(user, owner_id: int) -> Owner:
    if not (
        getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or user.has_perm("accounts.receive_without_order")
        )
    ):
        raise PermissionDenied("没有无订单收货权限。")
    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        raise PermissionDenied("账号没有有效的数据范围。")
    owner = Owner.objects.filter(pk=owner_id, is_active=True, is_deleted=False).first()
    if owner is None:
        raise PermissionDenied("货主不存在或已停用。")
    if scope.is_global or owner_id in scope.owner_ids:
        return owner
    if owner_id in owner_ids_for_warehouses(scope.warehouse_ids):
        return owner
    raise PermissionDenied("无权为该货主查询或创建商品。")


def find_owner_product(owner_id: int, barcode: str):
    variants = equivalent_gtins(barcode)
    registries = list(
        ProductIdentifierRegistry.objects.filter(
            owner_id=owner_id, normalized_value__in=variants
        ).select_related("product__base_uom")
    )
    products = {row.product_id: row.product for row in registries}
    if len(products) > 1:
        raise ValidationError("该 GTIN 的等价编码匹配到多个商品，请先清理标识冲突。")
    if not products:
        return None
    product = next(iter(products.values()))
    if product.is_deleted or not product.is_active:
        raise ValidationError("该条码已被停用或删除的商品占用，不能重复建档。")
    return product


def receive_product_card(product: Product) -> dict:
    base_uom = product.base_uom
    image_url = product.product_image.url if product.product_image else None
    unit_option = {
        "key": "BASE",
        "kind": "base",
        "label": base_uom.name or base_uom.code,
        "multiplier": 1,
        "package_id": None,
        "barcode": None,
    }
    return {
        "id": product.pk,
        "sku": product.sku or product.code,
        "name": product.name,
        "spec": product.spec,
        "base_unit": base_uom.code,
        "base_unit_name": base_uom.name,
        "carton_unit": None,
        "carton_conv": None,
        "price": product.price or 0,
        "product_image_url": image_url,
        "gtin": product.gtin,
        "aux_uom_name": None,
        "aux_qty_in_base": None,
        "max_discount": product.max_discount,
        "product_min_price": product.min_price,
        "minimum_sale_price": None,
        "packaging": [],
        "unitOptions": [unit_option],
        "selectedUnitIndex": 0,
        "base_quantity": 0,
    }


def _exact_brand(name: str):
    if not name:
        return None
    matches = list(
        Brand.objects.filter(name__iexact=name, is_active=True, is_deleted=False)[:2]
    )
    return matches[0] if len(matches) == 1 else None


@transaction.atomic
def quick_create_product(*, owner: Owner, lookup_id, values: dict, user, request=None):
    cache = (
        Gs1LookupCache.objects.select_for_update()
        .filter(
            pk=lookup_id,
            status=Gs1LookupCache.Status.SUCCESS,
            found=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if cache is None:
        raise ValidationError("GS1 查询结果不存在、未命中或已失效，请重新扫码。")

    existing = find_owner_product(owner.pk, cache.query_code)
    if existing is not None:
        return existing, False

    category = (
        ProductCategory.objects.select_related("parent__parent")
        .filter(pk=values["category_id"], is_active=True, is_deleted=False)
        .first()
    )
    if category is None or not category.has_active_path():
        raise ValidationError({"category_id": "请选择层级全部启用的商品分类。"})
    base_uom = ProductUom.objects.filter(
        pk=values["base_uom_id"], is_active=True, is_deleted=False
    ).first()
    if base_uom is None:
        raise ValidationError({"base_uom_id": "请选择有效的基本单位。"})

    locked_owner = Owner.all_objects.select_for_update().get(pk=owner.pk)
    sequence = locked_owner.next_sku_sequence
    while True:
        generated = format_owner_sequence_identifier(locked_owner.code, sequence)
        occupied = (
            ProductIdentifierRegistry.objects.filter(
                owner_id=owner.pk, normalized_value=generated
            ).exists()
            or Product.all_objects.filter(owner_id=owner.pk)
            .filter(Q(code__iexact=generated) | Q(sku__iexact=generated))
            .exists()
        )
        if not occupied:
            break
        sequence += 1
    if sequence != locked_owner.next_sku_sequence:
        locked_owner.next_sku_sequence = sequence
        locked_owner.save(update_fields=["next_sku_sequence", "updated_at"])

    data = dict(cache.payload or {})
    gtin = str(data.get("barcode") or cache.query_code).strip()
    if not (gtin.isdigit() and len(gtin) in (8, 12, 13, 14)):
        gtin = cache.canonical_gtin
    name = str(
        data.get("name") or data.get("general_name") or f"GS1商品 {gtin}"
    ).strip()[:200]
    spec = (
        str(data.get("specification") or data.get("net_content") or "").strip()[:200]
        or None
    )
    manufacturer = str(data.get("manufacturer") or "").strip()[:200] or None
    batch_control = bool(values["batch_control"])
    expiry_control = bool(values["expiry_control"])
    extra = {
        "gs1": {
            "provider": "apizero",
            "provider_request_id": cache.provider_request_id,
            "lookup_id": str(cache.pk),
            "fetched_at": cache.fetched_at.isoformat() if cache.fetched_at else None,
            "registered": bool(cache.registered),
            "payload": data,
        }
    }
    product = Product(
        owner=locked_owner,
        code=generated,
        name=name,
        spec=spec,
        vender=manufacturer,
        brand=_exact_brand(str(data.get("brand") or "").strip()),
        category=category,
        base_uom=base_uom,
        gtin=gtin,
        batch_control=batch_control,
        expiry_control=expiry_control,
        expiry_basis=values.get("expiry_basis") if expiry_control else None,
        shelf_life_days=values.get("shelf_life_days") if expiry_control else None,
        inbound_valid_days=values.get("inbound_valid_days") if expiry_control else None,
        expiry_warning_days=(
            values.get("expiry_warning_days") if expiry_control else None
        ),
        fefo_required=expiry_control,
        extra=extra,
        created_by=user,
        updated_by=user,
    )
    product.full_clean()
    product.save()
    record_audit_event(
        action="inbound.gs1_product.quick_create",
        module="inbound",
        request=request,
        user=user,
        obj=product,
        after={
            "code": product.code,
            "sku": product.sku,
            "gtin": product.gtin,
            "registered": bool(cache.registered),
        },
        metadata={
            "lookup_id": str(cache.pk),
            "provider_request_id": cache.provider_request_id,
        },
    )
    return product, True
