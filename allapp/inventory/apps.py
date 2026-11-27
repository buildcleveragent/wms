from django.apps import AppConfig
from django.db.models.signals import pre_delete


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "allapp.inventory"
    verbose_name = "库存管理"  # 左侧一级菜单中文

    def ready(self):
        from .models import InventoryDetail, record_deleted_inventory_balance

        pre_delete.connect(
            record_deleted_inventory_balance,
            sender=InventoryDetail,
            dispatch_uid="inventory.record_deleted_balance",
        )
