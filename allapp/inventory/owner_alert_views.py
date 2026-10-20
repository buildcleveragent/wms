"""Owner-only inventory alert endpoints and audited optimistic configuration updates."""

import hashlib
import json

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner

from .models import OwnerInventoryAlertPolicy, ProductInventoryAlertPolicy
from .owner_permissions import owner_inventory_scope
from .owner_alerts import (
    CATEGORIES,
    DEFAULTS,
    alert_products,
    alert_response,
    effective,
    product_info,
)


class Conflict(APIException):
    status_code = 409
    default_detail = "配置已被修改，请重新加载后再保存"


def alert_scope(user, write=False):
    scope = owner_inventory_scope(user)
    can_edit = "owner_manager" in scope.roles and user.has_perm("accounts.access_owner_management")
    if write and not can_edit:
        raise PermissionDenied("仅有货主管理权限的管理员可以修改预警配置")
    return scope.single_owner_id, can_edit


class PageFilters(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=100)
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=20, min_value=1, max_value=100)


class AlertFilters(PageFilters):
    category = serializers.ChoiceField(choices=tuple(CATEGORIES))
    status = serializers.ChoiceField(choices=("confirmed", "needs_review"), default="confirmed")
    expiry = serializers.ChoiceField(choices=("all", "expiring", "expired"), default="all")


class OwnerConfigInput(serializers.Serializer):
    version = serializers.CharField(max_length=64)
    low_enabled = serializers.BooleanField(required=False)
    expiry_enabled = serializers.BooleanField(required=False)
    slow_enabled = serializers.BooleanField(required=False)
    expiry_days = serializers.IntegerField(required=False, min_value=1, max_value=3650)
    slow_days = serializers.IntegerField(required=False, min_value=1, max_value=3650)


class ProductConfigInput(serializers.Serializer):
    version = serializers.CharField(max_length=64)
    min_stock = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, allow_null=True, required=False
    )
    expiry_mode = serializers.ChoiceField(choices=("inherit", "custom", "off"), required=False)
    expiry_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=3650
    )
    slow_mode = serializers.ChoiceField(choices=("inherit", "custom", "off"), required=False)
    slow_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=3650
    )


def version(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def owner_config(owner_id):
    row = OwnerInventoryAlertPolicy.objects.filter(owner_id=owner_id).first()
    values = {key: getattr(row, key, value) for key, value in DEFAULTS.items()}
    return {**values, "version": version([owner_id, getattr(row, "revision", 0), values])}


def product_config(product, owner_values=None):
    owner_values = owner_values or owner_config(product.owner_id)
    row = ProductInventoryAlertPolicy.objects.filter(product_id=product.pk).first()
    values = {"min_stock": str(product.min_stock) if product.min_stock is not None else None}
    for key in ("expiry", "slow"):
        values[f"{key}_mode"] = getattr(row, f"{key}_mode", "inherit")
        values[f"{key}_days"] = getattr(row, f"{key}_days", None)
    return {
        **values,
        "product": product_info(product),
        "defaults": owner_values,
        "effective": effective(product, owner_values, row),
        "version": version(
            [
                product.pk,
                product.updated_at,
                getattr(row, "revision", 0),
                values,
                owner_values["version"],
            ]
        ),
    }


class ScopedAlertView(APIView):
    permission_classes = [IsAuthenticated]


class OwnerAlertsView(ScopedAlertView):
    def get(self, request):
        owner_id, can_edit = alert_scope(request.user)
        return Response({**alert_response(owner_id), "can_edit": can_edit})


class OwnerAlertItemsView(ScopedAlertView):
    def get(self, request):
        owner_id, can_edit = alert_scope(request.user)
        inputs = AlertFilters(data=request.query_params)
        inputs.is_valid(raise_exception=True)
        return Response({**alert_response(owner_id, inputs.validated_data), "can_edit": can_edit})


class OwnerAlertSettingsView(ScopedAlertView):
    def get(self, request):
        owner_id, can_edit = alert_scope(request.user)
        return Response({**owner_config(owner_id), "can_edit": can_edit})

    @transaction.atomic
    def patch(self, request):
        owner_id, _ = alert_scope(request.user, write=True)
        inputs = OwnerConfigInput(data=request.data)
        inputs.is_valid(raise_exception=True)
        # The existing owner row serializes first creation as well as subsequent updates.
        Owner.objects.select_for_update().get(pk=owner_id)
        before = owner_config(owner_id)
        values = dict(inputs.validated_data)
        if values.pop("version") != before["version"]:
            raise Conflict()
        row = OwnerInventoryAlertPolicy.objects.filter(owner_id=owner_id).first()
        row = row or OwnerInventoryAlertPolicy(owner_id=owner_id, revision=0)
        for key, value in values.items():
            setattr(row, key, value)
        row.revision += 1
        row.save()
        after = owner_config(owner_id)
        record_audit_event(
            action="inventory.alerts.settings.update",
            module="inventory.owner_alerts",
            request=request,
            obj=row,
            owner_id=owner_id,
            before=before,
            after=after,
        )
        return Response({**after, "can_edit": True})


class OwnerAlertProductsView(ScopedAlertView):
    def get(self, request):
        owner_id, can_edit = alert_scope(request.user)
        inputs = PageFilters(data=request.query_params)
        inputs.is_valid(raise_exception=True)
        filters = inputs.validated_data
        qs = alert_products(owner_id)
        if filters.get("search"):
            q = filters["search"]
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(sku__icontains=q))
        count = qs.count()
        start = (filters["page"] - 1) * filters["page_size"]
        return Response(
            {
                "count": count,
                "can_edit": can_edit,
                "results": [product_info(p) for p in qs[start : start + filters["page_size"]]],
                "next_page": filters["page"] + 1 if start + filters["page_size"] < count else None,
            }
        )


class OwnerAlertProductSettingsView(ScopedAlertView):
    def get(self, request, product_id):
        owner_id, can_edit = alert_scope(request.user)
        product = get_object_or_404(
            alert_products(owner_id),
            pk=product_id,
            owner_id=owner_id,
        )
        return Response({**product_config(product), "can_edit": can_edit})

    @transaction.atomic
    def patch(self, request, product_id):
        owner_id, _ = alert_scope(request.user, write=True)
        inputs = ProductConfigInput(data=request.data)
        inputs.is_valid(raise_exception=True)
        Owner.objects.select_for_update().get(pk=owner_id)
        product = get_object_or_404(
            alert_products(owner_id).select_for_update(),
            pk=product_id,
            owner_id=owner_id,
        )
        before = product_config(product)
        values = dict(inputs.validated_data)
        if values.pop("version") != before["version"]:
            raise Conflict()
        merged = {**before, **values}
        for key in ("expiry", "slow"):
            if merged[f"{key}_mode"] == "custom" and merged[f"{key}_days"] is None:
                raise serializers.ValidationError({f"{key}_days": "自定义阈值必须填写天数"})
            if merged[f"{key}_mode"] != "custom":
                merged[f"{key}_days"] = None
        if "min_stock" in values:
            minimum = values["min_stock"]
            if (
                minimum is not None
                and product.max_stock is not None
                and minimum >= product.max_stock
            ):
                raise serializers.ValidationError({"min_stock": "最低库存必须小于最高库存"})
            product.min_stock = minimum
            product.updated_by = request.user
            product.save(update_fields=["min_stock", "updated_by", "updated_at"])
        row = ProductInventoryAlertPolicy.objects.filter(product_id=product.pk).first()
        row = row or ProductInventoryAlertPolicy(product=product, revision=0)
        for key in ("expiry_mode", "expiry_days", "slow_mode", "slow_days"):
            setattr(row, key, merged[key])
        row.revision += 1
        row.save()
        after = product_config(product)
        record_audit_event(
            action="inventory.alerts.product.update",
            module="inventory.owner_alerts",
            request=request,
            obj=row,
            owner_id=owner_id,
            before=before,
            after=after,
        )
        return Response({**after, "can_edit": True})
