"""Stock fixtures for owner purchasing tests that require a sellable balance."""

from decimal import Decimal

from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse


def stock_for_owner_order(product, warehouse, quantity="1000"):
    """Provide real current stock without bypassing the submission checks."""
    sub, _ = Subwarehouse.objects.get_or_create(
        warehouse=warehouse, code=f"B{warehouse.pk}", defaults={"name": "采购测试子仓"}
    )
    location, _ = Location.objects.get_or_create(
        warehouse=warehouse,
        code=f"B{warehouse.pk}-01-01-01",
        defaults={"subwarehouse": sub, "name": "采购测试库位"},
    )
    return InventoryDetail.objects.create(
        owner=product.owner,
        product=product,
        warehouse=warehouse,
        subwarehouse=sub,
        location=location,
        onhand_qty=Decimal(quantity),
    )
