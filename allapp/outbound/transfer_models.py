"""Auditable ownership transfers backing an assisted customer sale."""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q

from allapp.core.models import BaseModel, SoftDeleteManager


class MappingQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {"is_active", "updated_at", "updated_by", "updated_by_id", "remark"}:
            raise ValidationError("转货商品对应不能改绑或删除；可以停用但不会解除保护。")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if set(fields) - {"is_active", "updated_at", "updated_by", "remark"}:
            raise ValidationError("转货商品对应不能批量改绑或删除。")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("请逐个保存商品对应以建立来源保护。")

    def delete(self):
        raise ValidationError("转货商品对应不能删除，请停用。")


class OwnerProductMapping(BaseModel):
    objects = SoftDeleteManager.from_queryset(MappingQuerySet)()
    all_objects = models.Manager.from_queryset(MappingQuerySet)()
    source_product = models.ForeignKey(
        "products.Product",
        verbose_name="来源商品",
        on_delete=models.PROTECT,
        related_name="outgoing_owner_mappings",
    )
    target_owner = models.ForeignKey(
        "baseinfo.Owner", verbose_name="目标货主", on_delete=models.PROTECT
    )
    target_product = models.ForeignKey(
        "products.Product",
        verbose_name="目标商品",
        on_delete=models.PROTECT,
        related_name="incoming_owner_mappings",
    )

    class Meta:
        verbose_name = "货主商品映射"
        verbose_name_plural = "货主商品映射"
        constraints = [
            models.UniqueConstraint(
                fields=["source_product", "target_owner"], name="uq_owner_product_mapping"
            )
        ]

    def clean(self):
        super().clean()
        if self.pk and not self._state.adding:
            old = type(self).all_objects.get(pk=self.pk)
            if any(
                getattr(self, field) != getattr(old, field)
                for field in (
                    "source_product_id",
                    "target_owner_id",
                    "target_product_id",
                    "is_deleted",
                )
            ):
                raise ValidationError("转货商品对应不能改绑或删除，请使用停用控制。")
        if self.target_product_id and self.target_owner_id:
            if self.target_product.owner_id != self.target_owner_id:
                raise ValidationError("目标商品必须属于目标货主。")
        if self.source_product_id and self.target_owner_id:
            if self.source_product.owner_id == self.target_owner_id:
                raise ValidationError("来源商品与目标货主不能相同。")

    @transaction.atomic
    def save(self, *args, **kwargs):
        from allapp.products.transfer_protection import bind

        self.full_clean()
        bind(self.source_product, self.target_product)
        if self._state.adding:
            kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("转货商品对应不能删除，请停用。")


class AssistedOwnershipTransfer(BaseModel):
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "待转货"
        POSTED = "POSTED", "已转货并出库"
        CANCELLED = "CANCELLED", "已取消"

    transfer_no = models.CharField("转货单号", max_length=64, unique=True)
    sales_order = models.OneToOneField(
        "outbound.OutboundOrder", on_delete=models.PROTECT, related_name="assisted_transfer"
    )
    source_owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, related_name="outgoing_assisted_transfers"
    )
    target_owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, related_name="incoming_assisted_transfers"
    )
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)
    posted_at = models.DateTimeField(null=True, blank=True)
    posting_batch = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        verbose_name = "代办货权转移单"
        verbose_name_plural = "代办货权转移单"
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_owner=F("target_owner")), name="ck_transfer_distinct_owners"
            )
        ]
        indexes = [
            models.Index(
                fields=["source_owner", "warehouse", "created_at"], name="ix_owner_transfer_source"
            )
        ]

    def __str__(self):
        return self.transfer_no


class AssistedOwnershipTransferLine(BaseModel):
    transfer = models.ForeignKey(
        AssistedOwnershipTransfer, on_delete=models.PROTECT, related_name="lines"
    )
    sales_line = models.OneToOneField(
        "outbound.OutboundOrderLine", on_delete=models.PROTECT, related_name="transfer_line"
    )
    source_product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    source_package = models.ForeignKey(
        "products.ProductPackage", on_delete=models.PROTECT, null=True, blank=True
    )
    package_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    quantity = models.DecimalField("转货数量", max_digits=18, decimal_places=3)
    transfer_price = models.DecimalField("转货基本单价", max_digits=18, decimal_places=4)

    class Meta:
        verbose_name = "代办转货明细"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="ck_transfer_line_qty"),
            models.CheckConstraint(condition=Q(transfer_price__gte=0), name="ck_transfer_price"),
        ]


class AssistedTransferReservation(BaseModel):
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "已预留"
        POSTED = "POSTED", "已过账"
        RELEASED = "RELEASED", "已释放"

    transfer_line = models.ForeignKey(
        AssistedOwnershipTransferLine, on_delete=models.PROTECT, related_name="reservations"
    )
    task_line = models.OneToOneField(
        "tasking.WmsTaskLine", on_delete=models.PROTECT, related_name="transfer_reservation"
    )
    source_inventory = models.ForeignKey("inventory.InventoryDetail", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    source_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="ck_transfer_reservation_qty")
        ]
