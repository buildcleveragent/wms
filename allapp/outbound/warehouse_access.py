"""Shared owner-to-warehouse authorization queries for outbound workflows."""

from allapp.locations.models import Warehouse


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


def owner_warehouse_ids(owner_id):
    """Return the authorized warehouse IDs for an owner."""

    return frozenset(owner_warehouse_queryset(owner_id).values_list("id", flat=True))


def owner_can_use_warehouse(owner_id, warehouse_id):
    """Return whether an owner may create business documents in a warehouse."""

    if not owner_id or not warehouse_id:
        return False
    return owner_warehouse_queryset(owner_id).filter(pk=warehouse_id).exists()
