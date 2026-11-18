"""Permanent provenance and master-data rules for reseller inventory products."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

DESCRIPTION_FIELDS = ("name", "spec", "brand_id", "model")
MEASUREMENT_FIELDS = (
    "base_uom_id",
    "serial_control",
    "batch_control",
    "expiry_control",
    "expiry_basis",
    "shelf_life_days",
    "inbound_valid_days",
    "expiry_warning_days",
    "fefo_required",
    "mix_lot_allowed",
    "mix_expiry_allowed",
)
PRODUCT_FIELDS = DESCRIPTION_FIELDS + MEASUREMENT_FIELDS
PACKAGE_FIELDS = ("product_id", "uom_id", "qty_in_base", "is_active", "is_deleted")
_writes = ContextVar("transfer_master_writes", default=frozenset())


@contextmanager
def _system_write(product_ids):
    """Only provenance services may synchronize or repair a managed product."""
    token = _writes.set(_writes.get() | frozenset(product_ids))
    try:
        yield
    finally:
        _writes.reset(token)


def bindings(product_id):
    from .models import TransferProductBinding

    return TransferProductBinding.objects.filter(
        Q(source_product_id=product_id) | Q(target_product_id=product_id)
    )


def protected_fields(product_id):
    rows = bindings(product_id)
    if rows.filter(target_product_id=product_id).exists():
        return PRODUCT_FIELDS
    if rows.exists():
        return MEASUREMENT_FIELDS
    return ()


def protection_info(product):
    from .models import TransferProductBinding

    cached = getattr(product, "_prefetched_objects_cache", {})
    rows = cached.get("transfer_sources")
    if rows is None:
        rows = TransferProductBinding.objects.filter(target_product=product).select_related(
            "source_product__owner"
        )
    sources = [
        {
            "product_id": row.source_product_id,
            "owner_id": row.source_product.owner_id,
            "label": f"{row.source_product.owner.name} / {row.source_product.name}",
        }
        for row in rows
    ]
    outgoing = (
        bool(cached["transfer_targets"])
        if "transfer_targets" in cached
        else (TransferProductBinding.objects.filter(source_product=product).exists())
    )
    fields = PRODUCT_FIELDS if sources else MEASUREMENT_FIELDS if outgoing else ()
    return {
        "managed": bool(sources),
        "sources": sources,
        "locked_fields": [field.removesuffix("_id") for field in fields],
        "reason": (
            "转货来源管理：商品身份及计量规则不能独立修改"
            if sources
            else "已用于跨货主转货，计量规则已锁定" if outgoing else ""
        ),
    }


def lock_family(product_ids, extra_owner_ids=()):
    """Use the same owner-before-product lock order as transfer creation."""
    from allapp.baseinfo.models import Owner

    from .models import Product, TransferProductBinding

    family = set(product_ids)
    while True:
        edges = TransferProductBinding.objects.filter(
            Q(source_product_id__in=family) | Q(target_product_id__in=family)
        ).values_list("source_product_id", "target_product_id")
        expanded = family | {pk for edge in edges for pk in edge}
        if expanded == family:
            break
        family = expanded
    owner_ids = set(Product.all_objects.filter(pk__in=family).values_list("owner_id", flat=True))
    owner_ids.update(extra_owner_ids)
    list(Owner.all_objects.select_for_update().filter(pk__in=owner_ids).order_by("pk"))
    while True:
        current_edges = TransferProductBinding.objects.filter(
            Q(source_product_id__in=family) | Q(target_product_id__in=family)
        ).values_list("source_product_id", "target_product_id")
        expanded = family | {pk for edge in current_edges for pk in edge}
        if expanded == family:
            break
        discovered_owners = set(
            Product.all_objects.filter(pk__in=expanded - family).values_list("owner_id", flat=True)
        )
        if not discovered_owners <= owner_ids:
            raise ValidationError("商品来源关系刚刚发生变化，请重试本次操作。")
        # A concurrent first transfer may have created the receiving archive while
        # we waited for these same owners. Their locks make including it safe.
        family = expanded
    list(Product.all_objects.select_for_update().filter(pk__in=family).order_by("pk"))


def validate_product(product, original=None, update_fields=None):
    from .models import Product

    if not product.pk or product._state.adding or product.pk in _writes.get():
        return
    original = original or Product.all_objects.get(pk=product.pk)
    fields = protected_fields(product.pk)
    if update_fields is not None:
        supplied = set(update_fields) | {f"{field}_id" for field in update_fields}
        fields = [field for field in fields if field in supplied]
    changed = [field for field in fields if getattr(original, field) != getattr(product, field)]
    if changed:
        raise ValidationError(
            {
                field.removesuffix("_id"): "转货商品字段受来源保护，不能直接修改。"
                for field in changed
            }
        )
    description_fields = DESCRIPTION_FIELDS
    if update_fields is not None:
        description_fields = [field for field in description_fields if field in supplied]
    if any(getattr(original, field) != getattr(product, field) for field in description_fields):
        from allapp.core.product_identity import require_product_snapshots

        from .models import TransferProductBinding

        descendants = set()
        pending = {product.pk}
        while pending:
            children = set(
                TransferProductBinding.objects.filter(source_product_id__in=pending).values_list(
                    "target_product_id", flat=True
                )
            )
            if product.pk in children:
                raise ValidationError("历史来源绑定存在循环，不能同步商品描述。")
            pending = children - descendants
            descendants |= children
        if descendants:
            require_product_snapshots(descendants)


def validate_package(package, update_fields=None):
    from .models import ProductPackage, TransferProductBinding

    if package.product_id in _writes.get():
        return
    original = None if package._state.adding else ProductPackage.all_objects.get(pk=package.pk)
    ids = {package.product_id} | ({original.product_id} if original else set())
    rows = TransferProductBinding.objects.filter(
        Q(source_product_id__in=ids) | Q(target_product_id__in=ids)
    )
    if not rows.exists():
        return
    if original is None:
        if rows.filter(target_product_id=package.product_id).exists():
            raise ValidationError("接收商品的包装由来源商品统一新增，不能独立添加。")
        return
    fields = PACKAGE_FIELDS
    if update_fields is not None:
        supplied = set(update_fields) | {f"{field}_id" for field in update_fields}
        fields = [field for field in fields if field in supplied]
    if any(getattr(package, field) != getattr(original, field) for field in fields):
        raise ValidationError("已绑定转货来源的包装单位、换算比例和启用状态不能修改或删除。")


def guard_bulk_products(queryset, fields):
    from .models import TransferProductBinding

    names = set(fields) | {f"{field}_id" for field in fields}
    ids = queryset.values("pk")
    scope = Q(target_product_id__in=ids)
    if names.intersection(MEASUREMENT_FIELDS):
        scope |= Q(source_product_id__in=ids)
    if names.intersection(PRODUCT_FIELDS) and TransferProductBinding.objects.filter(scope).exists():
        raise ValidationError("批量操作包含受转货来源保护的商品字段。")


def guard_bulk_packages(queryset, fields):
    from .models import TransferProductBinding

    names = set(fields) | {f"{field}_id" for field in fields}
    if "product_id" in names:
        # The destination may be an expression or another protected archive.
        # Individual saves validate both ends of a reassignment.
        raise ValidationError("包装不能通过批量操作改绑商品，请逐条保存以校验双方来源保护。")
    if names.intersection(PACKAGE_FIELDS):
        ids = queryset.values("product_id")
        if TransferProductBinding.objects.filter(
            Q(source_product_id__in=ids) | Q(target_product_id__in=ids)
        ).exists():
            raise ValidationError("批量操作包含受转货来源保护的包装。")


def differences(source, target, *, packages=True):
    """Report concrete differences; never silently reinterpret historical quantities."""
    result = []
    for field in PRODUCT_FIELDS:
        before, after = getattr(source, field), getattr(target, field)
        if before != after:
            label = source._meta.get_field(field.removesuffix("_id")).verbose_name
            if field == "base_uom_id":
                before = f"{source.base_uom.name}（{before}）"
                after = f"{target.base_uom.name}（{after}）"
            result.append(f"{label}: 来源={before!s}，接收={after!s}")
    if packages:
        source_packs = {
            p.uom_id: p
            for p in source.packages.filter(is_active=True, uom__is_active=True).select_related(
                "uom"
            )
        }
        target_packs = {
            p.uom_id: p
            for p in target.packages.filter(is_active=True, uom__is_active=True).select_related(
                "uom"
            )
        }
        for uom_id in sorted(source_packs.keys() | target_packs.keys()):
            a, b = source_packs.get(uom_id), target_packs.get(uom_id)
            if a is None or b is None or a.qty_in_base != b.qty_in_base:
                result.append(
                    f"包装单位 {(a or b).uom.name}（{uom_id}）: 来源={a.qty_in_base if a else '缺失'}，"
                    f"接收={b.qty_in_base if b else '缺失'}"
                )
    return result


@transaction.atomic
def bind(source, target):
    from .models import TransferProductBinding

    lock_family([source.pk, target.pk])
    if source.owner_id == target.owner_id:
        raise ValidationError("转货来源和接收商品必须属于不同货主。")
    if (
        TransferProductBinding.objects.filter(
            source_product=source, target_product__owner_id=target.owner_id
        )
        .exclude(target_product=target)
        .exists()
    ):
        raise ValidationError("同一来源在接收货主下存在多个商品档案，请先核查历史对应。")
    if (
        TransferProductBinding.objects.filter(target_product=target)
        .exclude(source_product=source)
        .exists()
    ):
        raise ValidationError("接收商品存在多个来源冲突，请先检查历史转货绑定。")
    validate_no_cycle(source.pk, target.pk)
    return TransferProductBinding.objects.get_or_create(
        source_product=source, target_product=target
    )[0]


def validate_no_cycle(source_id, target_id):
    from .models import TransferProductBinding

    descendants = {target_id}
    while True:
        expanded = descendants | set(
            TransferProductBinding.objects.filter(source_product_id__in=descendants).values_list(
                "target_product_id", flat=True
            )
        )
        if source_id in expanded:
            raise ValidationError("商品转货来源不能形成循环绑定。")
        if expanded == descendants:
            break
        descendants = expanded


def sync_description(product, original):
    from .models import TransferProductBinding

    changed = [
        field for field in DESCRIPTION_FIELDS if getattr(product, field) != getattr(original, field)
    ]
    if not changed:
        return
    for row in TransferProductBinding.objects.filter(source_product=product).select_related(
        "target_product"
    ):
        target = row.target_product
        if target.pk in _writes.get():
            raise ValidationError("历史来源绑定存在循环，不能同步商品描述。")
        if (
            TransferProductBinding.objects.filter(target_product=target)
            .exclude(source_product=product)
            .exists()
        ):
            raise ValidationError("接收商品存在多个来源，不能同步商品描述。")
        conflicts = differences(original, target)
        if conflicts:
            raise ValidationError(
                f"来源商品 {product.pk} 与接收商品 {target.pk} 已有历史差异，请先审核修复："
                + "；".join(conflicts)
            )
        with _system_write([target.pk]):
            for field in changed:
                setattr(target, field, getattr(product, field))
            target.save(update_fields=[field.removesuffix("_id") for field in changed])


def sync_new_package(package):
    from .models import ProductPackage, TransferProductBinding

    for row in TransferProductBinding.objects.filter(source_product_id=package.product_id):
        if (
            TransferProductBinding.objects.filter(target_product_id=row.target_product_id)
            .exclude(source_product_id=package.product_id)
            .exists()
        ):
            raise ValidationError("接收商品存在多个来源，不能同步新增包装。")
        existing = ProductPackage.all_objects.filter(
            product_id=row.target_product_id, uom_id=package.uom_id
        ).first()
        if existing:
            if (
                existing.is_deleted
                or not existing.is_active
                or existing.qty_in_base != package.qty_in_base
            ):
                raise ValidationError("接收商品存在不一致的包装，不能同步新增包装。")
            continue
        with _system_write([row.target_product_id]):
            ProductPackage.objects.create(
                product_id=row.target_product_id,
                uom_id=package.uom_id,
                qty_in_base=package.qty_in_base,
                is_pickable=package.is_pickable,
                sort_order=package.sort_order,
            )
