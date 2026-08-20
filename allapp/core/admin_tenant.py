"""Fail-closed tenant boundaries shared by Django Admin registrations."""

from __future__ import annotations

from functools import wraps

from django.contrib import admin
from django.core.checks import Error, Tags, register
from django.core.exceptions import PermissionDenied

from allapp.accounts.access import AccessScope
from allapp.baseinfo.owner_warehouse_access import (
    owner_ids_for_warehouses,
    owner_warehouse_ids,
)

# Explicit policies are deliberate: tenant scope must never be guessed from a
# similarly named field on a new model.
TENANT_ADMIN_POLICIES = {
    "products.product": {"owner": "owner_id"},
    "products.productpackage": {"owner": "product__owner_id"},
    "products.productbarcode": {"owner": "owner_id"},
    "products.productexternalidentifier": {"owner": "owner_id"},
    "baseinfo.owner": {"owner": "pk"},
    "baseinfo.ownerwarehousebinding": {
        "owner": "owner_id",
        "warehouse": "warehouse_id",
    },
    "baseinfo.customer": {"owner": "owner_id"},
    "baseinfo.supplier": {"owner": "owner_id"},
    "baseinfo.employee": {"warehouse": "warehouse_id"},
    "baseinfo.carriercompany": {
        "owner": "owner_id",
        "warehouse": "warehouse_id",
    },
    "baseinfo.driver": {
        "owner": "carrier_company__owner_id",
        "warehouse": "carrier_company__warehouse_id",
    },
    "baseinfo.vehicle": {
        "owner": "carrier_company__owner_id",
        "warehouse": "carrier_company__warehouse_id",
    },
    "locations.warehouse": {"warehouse": "pk"},
    "locations.subwarehouse": {"warehouse": "warehouse_id"},
    "locations.location": {"warehouse": "warehouse_id"},
    "locations.container": {"owner": "owner_id", "warehouse": "warehouse_id"},
}


def _model_label(model) -> str:
    return model._meta.label_lower


def _product_global_access(user, *, for_write: bool) -> bool:
    from allapp.products.permissions import (
        can_manage_all_owner_products,
        can_view_all_owner_products,
    )

    return can_manage_all_owner_products(user) if for_write else can_view_all_owner_products(user)


def scope_admin_queryset(user, queryset, *, for_write: bool = False):
    """Scope a configured model queryset or fail closed."""

    policy = TENANT_ADMIN_POLICIES.get(_model_label(queryset.model))
    if policy is None:
        return queryset.none()

    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        return queryset.none()
    if scope.is_global:
        return queryset
    if _model_label(queryset.model).startswith("products.") and _product_global_access(
        user, for_write=for_write
    ):
        return queryset

    owner_field = policy.get("owner")
    warehouse_field = policy.get("warehouse")
    if scope.owner_ids:
        filters = {}
        if owner_field:
            filters[f"{owner_field}__in"] = scope.owner_ids
        if warehouse_field:
            warehouse_ids = set()
            for owner_id in scope.owner_ids:
                warehouse_ids.update(owner_warehouse_ids(owner_id))
            filters[f"{warehouse_field}__in"] = warehouse_ids
        return queryset.filter(**filters) if filters else queryset.none()

    if scope.warehouse_ids:
        filters = {}
        if warehouse_field:
            filters[f"{warehouse_field}__in"] = scope.warehouse_ids
        if owner_field:
            filters[f"{owner_field}__in"] = owner_ids_for_warehouses(scope.warehouse_ids)
        return queryset.filter(**filters) if filters else queryset.none()
    return queryset.none()


def admin_object_in_scope(user, obj, *, for_write: bool = False) -> bool:
    if obj is None:
        return False
    if getattr(obj, "pk", None):
        queryset = obj.__class__._default_manager.filter(pk=obj.pk)
        return scope_admin_queryset(user, queryset, for_write=for_write).exists()

    policy = TENANT_ADMIN_POLICIES.get(_model_label(obj.__class__))
    if policy is None:
        return False
    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        return False
    if scope.is_global:
        return True
    if _model_label(obj.__class__).startswith("products.") and _product_global_access(
        user, for_write=for_write
    ):
        return True

    def resolve(path):
        value = obj
        for part in path.split("__"):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    owner_id = resolve(policy["owner"]) if policy.get("owner") else None
    warehouse_id = resolve(policy["warehouse"]) if policy.get("warehouse") else None
    if scope.owner_ids:
        if owner_id is not None and int(owner_id) not in scope.owner_ids:
            return False
        if warehouse_id is not None and not any(
            int(warehouse_id) in owner_warehouse_ids(allowed_owner)
            for allowed_owner in scope.owner_ids
        ):
            return False
        return owner_id is not None or warehouse_id is not None
    if scope.warehouse_ids:
        if warehouse_id is not None and int(warehouse_id) not in scope.warehouse_ids:
            return False
        if owner_id is not None and int(owner_id) not in owner_ids_for_warehouses(
            scope.warehouse_ids
        ):
            return False
        return owner_id is not None or warehouse_id is not None
    return False


class TenantScopedAdminMixin:
    """Apply one tenant policy to every Admin read and write surface."""

    def get_queryset(self, request):
        return scope_admin_queryset(request.user, super().get_queryset(request))

    def has_view_permission(self, request, obj=None):
        allowed = super().has_view_permission(request, obj)
        return bool(allowed and (obj is None or admin_object_in_scope(request.user, obj)))

    def has_change_permission(self, request, obj=None):
        allowed = super().has_change_permission(request, obj)
        return bool(
            allowed and (obj is None or admin_object_in_scope(request.user, obj, for_write=True))
        )

    def has_delete_permission(self, request, obj=None):
        allowed = super().has_delete_permission(request, obj)
        return bool(
            allowed and (obj is None or admin_object_in_scope(request.user, obj, for_write=True))
        )

    def has_add_permission(self, request):
        return bool(
            super().has_add_permission(request) and AccessScope.for_user(request.user).is_valid
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related_model = db_field.remote_field.model
        if _model_label(related_model) in TENANT_ADMIN_POLICIES:
            kwargs["queryset"] = scope_admin_queryset(
                request.user,
                related_model._default_manager.all(),
                for_write=True,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related_model = db_field.remote_field.model
        if _model_label(related_model) in TENANT_ADMIN_POLICIES:
            kwargs["queryset"] = scope_admin_queryset(
                request.user,
                related_model._default_manager.all(),
                for_write=True,
            )
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not admin_object_in_scope(request.user, obj, for_write=True):
            raise PermissionDenied("Object is outside the active tenant scope.")
        return super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        for inline_form in formset.forms:
            cleaned_data = getattr(inline_form, "cleaned_data", None) or {}
            if not cleaned_data or cleaned_data.get("DELETE"):
                continue
            instance = inline_form.instance
            if _model_label(
                instance.__class__
            ) in TENANT_ADMIN_POLICIES and not admin_object_in_scope(
                request.user, instance, for_write=True
            ):
                raise PermissionDenied("Inline object is outside the active tenant scope.")
        return super().save_formset(request, form, formset, change)

    def get_actions(self, request):
        actions = super().get_actions(request)
        wrapped = {}
        for name, (func, action_name, description) in actions.items():

            @wraps(func)
            def scoped_action(modeladmin, action_request, queryset, _func=func):
                queryset = scope_admin_queryset(action_request.user, queryset, for_write=True)
                return _func(modeladmin, action_request, queryset)

            wrapped[name] = (scoped_action, action_name, description)
        return wrapped


class TenantScopedInlineMixin:
    def get_queryset(self, request):
        return scope_admin_queryset(request.user, super().get_queryset(request))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related_model = db_field.remote_field.model
        if _model_label(related_model) in TENANT_ADMIN_POLICIES:
            kwargs["queryset"] = scope_admin_queryset(
                request.user,
                related_model._default_manager.all(),
                for_write=True,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@register(Tags.security, deploy=True)
def check_tenant_admin_policies(app_configs, **kwargs):
    errors = []
    for model, model_admin in admin.site._registry.items():
        if _model_label(model) not in TENANT_ADMIN_POLICIES:
            continue
        if not isinstance(model_admin, TenantScopedAdminMixin):
            errors.append(
                Error(
                    f"{model._meta.label} Admin is missing TenantScopedAdminMixin.",
                    id="wmsadmin.E001",
                )
            )
    return errors
