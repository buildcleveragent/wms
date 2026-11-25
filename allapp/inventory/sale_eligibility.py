"""Sale eligibility shared by catalogs, allocation, picking and inventory posting."""

from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone

from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location


def sale_layers(queryset=None, *, now=None):
    """Return sellable layers on the Shanghai business date, including expiry day."""
    now = now or timezone.now()
    zone = ZoneInfo("Asia/Shanghai")
    today = (now.replace(tzinfo=zone) if timezone.is_naive(now) else now.astimezone(zone)).date()
    queryset = InventoryDetail.objects.all() if queryset is None else queryset
    return queryset.filter(
        Q(product__expiry_control=False)
        | (
            Q(expiry_date__gte=today)
            & (~Q(product__expiry_basis="MFG") | Q(production_date__isnull=False))
        ),
        is_active=True,
        is_deleted=False,
        product__owner_id=F("owner_id"),
        location__warehouse_id=F("warehouse_id"),
        location__is_active=True,
        location__is_deleted=False,
        location__is_disabled=False,
        location__is_frozen=False,
    )


def locked_sale_layer_ids(details):
    """Recheck eligibility while holding the warehouse, inventory and location locks."""
    locations = sorted({detail.location_id for detail in details})
    list(
        Location.all_objects.select_for_update()
        .filter(pk__in=locations)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return set(
        sale_layers()
        .select_for_update()
        .filter(pk__in=[d.pk for d in details])
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def require_sale_layers(details):
    details = list(details)
    if locked_sale_layer_ids(details) != {detail.pk for detail in details}:
        raise ValidationError(
            "预留库存已过期、效期资料不全或库位不可用。请仓管核对实物并重配失效库存。"
        )


def selling_task(task):
    if task.task_type not in ("PICK", "DISPATCH"):
        return False
    if task.source_app == "pos":
        return True
    from allapp.outbound.services import _order_for_outbound_task

    order = _order_for_outbound_task(task)
    return bool(order and order.outbound_type == "SALES")
