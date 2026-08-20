"""Canonical fail-closed product tenant scoping."""

from __future__ import annotations

from django.db.models import QuerySet

from allapp.accounts.access import AccessScope
from allapp.baseinfo.owner_warehouse_access import owner_ids_for_warehouses

from .models import Product
from .permissions import can_manage_all_owner_products, can_view_all_owner_products


def allowed_product_owner_ids(user, *, for_write: bool = False) -> frozenset[int] | None:
    """Return allowed owner IDs, ``None`` for an explicitly global capability."""

    has_global_access = (
        can_manage_all_owner_products(user) if for_write else can_view_all_owner_products(user)
    )
    if has_global_access:
        return None

    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        return frozenset()
    if scope.owner_ids:
        return scope.owner_ids
    if scope.warehouse_ids:
        return owner_ids_for_warehouses(scope.warehouse_ids)
    return frozenset()


def scoped_product_queryset(
    user,
    queryset: QuerySet | None = None,
    *,
    for_write: bool = False,
) -> QuerySet:
    """Apply the same product boundary to APIs, Admin, autocomplete and exports."""

    queryset = queryset if queryset is not None else Product.objects.all()
    owner_ids = allowed_product_owner_ids(user, for_write=for_write)
    if owner_ids is None:
        return queryset
    if not owner_ids:
        return queryset.none()
    return queryset.filter(owner_id__in=owner_ids)
