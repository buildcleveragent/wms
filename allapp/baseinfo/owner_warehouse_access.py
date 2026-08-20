"""Shared queries for explicit owner-to-warehouse business authorization."""

from __future__ import annotations

from collections.abc import Iterable

from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse


def owner_queryset_for_warehouses(warehouse_ids: Iterable[int]):
    """Return active owners explicitly authorized for active warehouses."""

    normalized_ids = tuple(int(value) for value in warehouse_ids if value)
    if not normalized_ids:
        return Owner.objects.none()
    return (
        Owner.objects.filter(
            is_active=True,
            warehouse_bindings__warehouse_id__in=normalized_ids,
            warehouse_bindings__warehouse__is_active=True,
            warehouse_bindings__warehouse__is_deleted=False,
            warehouse_bindings__is_active=True,
            warehouse_bindings__is_deleted=False,
        )
        .distinct()
        .order_by("id")
    )


def owner_ids_for_warehouses(warehouse_ids: Iterable[int]) -> frozenset[int]:
    """Return IDs from explicit active owner-to-warehouse bindings."""

    return frozenset(owner_queryset_for_warehouses(warehouse_ids).values_list("id", flat=True))


def owner_warehouse_queryset(owner_id):
    """Return active warehouses explicitly enabled for one active owner."""

    if not owner_id:
        return Warehouse.objects.none()
    return (
        Warehouse.objects.filter(
            is_active=True,
            owner_bindings__owner_id=owner_id,
            owner_bindings__owner__is_active=True,
            owner_bindings__owner__is_deleted=False,
            owner_bindings__is_active=True,
            owner_bindings__is_deleted=False,
        )
        .distinct()
        .order_by("code", "id")
    )


def owner_warehouse_ids(owner_id) -> frozenset[int]:
    """Return the explicitly authorized warehouse IDs for an owner."""

    return frozenset(owner_warehouse_queryset(owner_id).values_list("id", flat=True))


def owner_can_use_warehouse(owner_id, warehouse_id) -> bool:
    """Return whether an active binding authorizes this business relationship."""

    if not owner_id or not warehouse_id:
        return False
    return owner_warehouse_queryset(owner_id).filter(pk=warehouse_id).exists()
