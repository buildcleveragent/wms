from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product, ProductPackage


class PosProduct(Product):
    class Meta:
        proxy = True
        verbose_name = "POS商品"
        verbose_name_plural = "POS商品"


class PosProductPackage(ProductPackage):
    class Meta:
        proxy = True
        verbose_name = "POS包装条码"
        verbose_name_plural = "POS包装条码"


class PosAvailableInventory(InventoryDetail):
    class Meta:
        proxy = True
        verbose_name = "POS可售库存"
        verbose_name_plural = "POS可售库存"


class PosSaleOrder(OutboundOrder):
    class Meta:
        proxy = True
        verbose_name = "POS销售单"
        verbose_name_plural = "POS销售单"


class PosSaleOrderLine(OutboundOrderLine):
    class Meta:
        proxy = True
        verbose_name = "POS销售明细"
        verbose_name_plural = "POS销售明细"
