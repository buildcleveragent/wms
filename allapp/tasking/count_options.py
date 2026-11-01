"""Permission-scoped choices and scope descriptions for PDA counting."""

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.choices import ZoneType
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.identifier_lookup import filter_by_product_search
from allapp.products.models import Product


def can_manage_count(user):
    """Use the same manager capability and tenant boundary as counting actions."""
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.is_superuser or user.has_perm("tasking.taskconfirm_as_wh_manager"))
        and AccessScope.for_user(user).is_valid
    )


class CountOptionsQuery(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=["metadata", "products", "locations"], default="metadata"
    )
    warehouse_id = serializers.IntegerField(min_value=1, required=False)
    owner_id = serializers.IntegerField(min_value=1, required=False)
    subwarehouse_id = serializers.IntegerField(min_value=1, required=False)
    zone_type = serializers.ChoiceField(choices=ZoneType.choices, required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    page = serializers.IntegerField(min_value=1, default=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=30)


def count_options(user, params):
    """Return allowed bindings, or a page of searchable products/locations."""
    if not can_manage_count(user):
        raise PermissionDenied("无盘点管理权限。")
    query = CountOptionsQuery(data=params)
    query.is_valid(raise_exception=True)
    values = query.validated_data
    scope = AccessScope.for_user(user)
    bindings = OwnerWarehouseBinding.objects.filter(is_active=True)
    if not scope.is_global:
        bindings = scope.filter_queryset(bindings)
    warehouses = Warehouse.objects.filter(pk__in=bindings.values("warehouse_id"), is_active=True)
    warehouse_id, owner_id = values.get("warehouse_id"), values.get("owner_id")
    if warehouse_id and not warehouses.filter(pk=warehouse_id).exists():
        raise PermissionDenied("仓库不在可用盘点范围内。")
    selected = bindings.filter(warehouse_id=warehouse_id) if warehouse_id else bindings
    owners = Owner.objects.filter(pk__in=selected.values("owner_id"), is_active=True)
    if owner_id and not owners.filter(pk=owner_id).exists():
        raise PermissionDenied("货主未授权使用所选仓库或不在可用范围内。")
    subwarehouses = Subwarehouse.objects.filter(warehouse_id=warehouse_id, is_active=True)
    subwarehouse_id = values.get("subwarehouse_id")
    if subwarehouse_id and not subwarehouses.filter(pk=subwarehouse_id).exists():
        raise serializers.ValidationError({"subwarehouse_id": "子仓不属于所选仓库。"})
    if values["kind"] == "metadata":
        return {
            "warehouses": list(warehouses.order_by("code").values("id", "code", "name")),
            "owners": (
                list(owners.order_by("code").values("id", "code", "name")) if warehouse_id else []
            ),
            "subwarehouses": list(subwarehouses.order_by("code").values("id", "code", "name")),
            "zones": [{"id": value, "name": str(label)} for value, label in ZoneType.choices],
        }
    if not warehouse_id or not owner_id:
        raise serializers.ValidationError("请先选择仓库和货主。")
    if values["kind"] == "products":
        rows = Product.objects.filter(owner_id=owner_id, is_active=True).select_related("brand")
        rows = filter_by_product_search(rows, values["search"], product_field="pk").order_by(
            "code", "id"
        )

        def render(product):
            return {
                "id": product.pk,
                "code": product.code,
                "sku": product.sku,
                "name": product.name,
                "brand_model": product.brand_model,
                "product_display_name": product.display_name,
                "spec": product.spec or "",
            }

    else:
        from django.db.models import Q

        rows = Location.objects.filter(warehouse_id=warehouse_id, is_active=True, is_disabled=False)
        if subwarehouse_id:
            rows = rows.filter(subwarehouse_id=subwarehouse_id)
        if "zone_type" in values:
            rows = rows.filter(zone_type=values["zone_type"])
        if values["search"]:
            rows = rows.filter(
                Q(code__icontains=values["search"]) | Q(name__icontains=values["search"])
            )
        rows = rows.order_by("code", "id")

        def render(location):
            return {"id": location.pk, "code": location.code, "name": location.name}

    start = (values["page"] - 1) * values["page_size"]
    page = list(rows[start : start + values["page_size"] + 1])
    return {
        "results": [render(row) for row in page[: values["page_size"]]],
        "next": values["page"] + 1 if len(page) > values["page_size"] else None,
    }


def count_scope_details(task):
    """Describe the stored scope without exposing book quantities to operators."""
    extra = task.counttaskextra
    payload = extra.scope_payload or {}
    labels = {}
    for key, model in (
        ("subwarehouse_id", Subwarehouse),
        ("location_id", Location),
        ("product_id", Product),
    ):
        if payload.get(key):
            obj = model.objects.filter(pk=payload[key]).first()
            if obj:
                labels[key] = (
                    f"{obj.code} · {obj.display_name if key == 'product_id' else obj.name}"
                )
    if payload.get("zone_type") is not None:
        labels["zone_type"] = str(
            dict(ZoneType.choices).get(payload["zone_type"], payload["zone_type"])
        )
    truncated = False
    if task.status in {"DRAFT", "READY"}:
        from allapp.tasking.counting import _inventory_queryset, _params_from_extra

        params = _params_from_extra(extra)
        truncated = _inventory_queryset(params)[: params.max_lines + 1].count() > params.max_lines
    return {
        "scope_truncated": truncated,
        "scope_payload": payload,
        "scope_labels": labels,
        "recount_threshold": str(extra.recount_threshold),
        "line_count": task.lines.count(),
    }
