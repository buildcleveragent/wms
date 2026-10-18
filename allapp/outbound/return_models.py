"""PDA physical returns; independent of sales and their approval workflow."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from allapp.core.models import BaseModel


class ReturnOrder(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        COMPLETED = "COMPLETED", "已完成"
        REVERSED = "REVERSED", "已冲销"

    class Recipient(models.TextChoices):
        OWNER = "OWNER", "退货主"
        SUPPLIER = "SUPPLIER", "退供应商"

    return_no = models.CharField(max_length=40, unique=True)
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT)
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT)
    recipient_type = models.CharField(max_length=10, choices=Recipient.choices)
    supplier = models.ForeignKey(
        "baseinfo.Supplier", null=True, blank=True, on_delete=models.PROTECT
    )
    recipient_name = models.CharField(max_length=200)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    confirm_key = models.UUIDField(null=True, blank=True)
    reverse_key = models.UUIDField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        permissions = [("process_pda_return", "办理PDA退货出库及本人单据冲销")]
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "operator", "request_id"], name="ux_pda_return_request"
            ),
            models.CheckConstraint(
                condition=(
                    Q(recipient_type="OWNER", supplier__isnull=True)
                    | Q(recipient_type="SUPPLIER", supplier__isnull=False)
                ),
                name="ck_pda_return_recipient",
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse", "confirmed_at"], name="ix_pda_return_confirm"),
            models.Index(fields=["warehouse", "owner", "status"], name="ix_pda_return_scope"),
        ]
        ordering = ["-id"]


class ReturnLine(models.Model):
    order = models.ForeignKey(ReturnOrder, related_name="lines", on_delete=models.CASCADE)
    inventory = models.ForeignKey("inventory.InventoryDetail", on_delete=models.PROTECT)
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    product_code = models.CharField(max_length=100)
    spec = models.CharField(max_length=200, blank=True)
    base_unit = models.CharField(max_length=30)
    location_name = models.CharField(max_length=200)
    dimensions = models.JSONField(default=dict)
    source_supplier_id = models.PositiveBigIntegerField(null=True)
    source_supplier_name = models.CharField(max_length=200, blank=True)
    available_qty = models.DecimalField(max_digits=18, decimal_places=4)
    damaged_qty = models.DecimalField(max_digits=18, decimal_places=4)
    new_damage_qty = models.DecimalField(max_digits=18, decimal_places=4)
    fifo_used = models.BooleanField(default=False)
    fifo_consumed = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "inventory"], name="ux_pda_return_inventory"),
            models.CheckConstraint(
                condition=Q(available_qty__gte=0, damaged_qty__gte=0, new_damage_qty__gte=0)
                & (Q(available_qty__gt=0) | Q(damaged_qty__gt=0) | Q(new_damage_qty__gt=0)),
                name="ck_pda_return_quantities",
            ),
        ]


class ReturnEvent(models.Model):
    """Append-only quality/physical movements, including zero-onhand damage recognition."""

    line = models.ForeignKey(ReturnLine, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(
        max_length=12,
        choices=[("DAMAGE", "现场破损认定"), ("CONFIRM", "退货出库"), ("REVERSE", "退货冲销")],
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    occurred_at = models.DateTimeField()
    reason = models.CharField(max_length=200)
    damaged_delta = models.DecimalField(max_digits=18, decimal_places=4)
    inventory_transaction = models.OneToOneField(
        "inventory.InventoryTransaction", null=True, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["line", "kind"], name="ux_pda_return_event")]
        indexes = [models.Index(fields=["occurred_at", "kind"], name="ix_pda_return_event_date")]
