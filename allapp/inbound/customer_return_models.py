"""Customer-to-warehouse returns, separate from PDA returns to suppliers/owners."""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import Q


def private_return_storage():
    location = Path(
        getattr(settings, "PRIVATE_RETURN_ROOT", Path(settings.BASE_DIR) / "private_returns")
    ).resolve()
    for name in ("MEDIA_ROOT", "STATIC_ROOT"):
        public = getattr(settings, name, None)
        if public:
            root = Path(public).resolve()
            if location == root or root in location.parents:
                raise ImproperlyConfigured("PRIVATE_RETURN_ROOT 必须位于公开媒体和静态目录之外。")
    return FileSystemStorage(
        file_permissions_mode=0o600,
        directory_permissions_mode=0o700,
        location=location,
    )


def return_photo_path(instance, filename):
    return f"{instance.order_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class CustomerReturn(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        OWNER_PENDING = "OWNER_PENDING", "待货主审核"
        WHS_PENDING = "WHS_PENDING", "待仓库确认"
        REJECTED = "REJECTED", "被退回"
        RECEIVING = "RECEIVING", "待到货"
        PARTIAL = "PARTIAL", "部分验收"
        POSTING = "POSTING", "验收完成待入账"
        COMPLETED = "COMPLETED", "已完成"
        CANCELLED = "CANCELLED", "已取消"
        CLOSED = "CLOSED", "已关闭剩余数量"

    number = models.CharField(max_length=40, unique=True)
    original_order = models.ForeignKey(
        "outbound.OutboundOrder", on_delete=models.PROTECT, related_name="customer_returns"
    )
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="assigned_customer_returns",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "客户退货申请"
        verbose_name_plural = "客户退货申请"
        permissions = [
            ("apply_customer_return", "申请客户退货"),
            ("approve_customer_return", "货主审核客户退货"),
            ("manage_customer_return", "仓库确认及关闭客户退货"),
            ("receive_customer_return", "验收客户退货及重试入账"),
        ]
        indexes = [
            models.Index(fields=["owner", "status", "id"]),
            models.Index(fields=["warehouse", "status", "id"]),
        ]


class CustomerReturnLine(models.Model):
    order = models.ForeignKey(CustomerReturn, on_delete=models.PROTECT, related_name="lines")
    original_line = models.ForeignKey("outbound.OutboundOrderLine", on_delete=models.PROTECT)
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    product_code = models.CharField(max_length=100)
    spec = models.CharField(max_length=200, blank=True)
    unit = models.CharField(max_length=30)
    qty = models.DecimalField(max_digits=18, decimal_places=4)
    reason_code = models.CharField(max_length=20)
    reason = models.CharField(max_length=500)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(qty__gt=0), name="cr_line_qty_positive")]


class CustomerReturnReceipt(models.Model):
    order = models.ForeignKey(CustomerReturn, on_delete=models.PROTECT, related_name="receipts")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True)
    posting_error = models.BooleanField(default=False)


class CustomerReturnReceiptLine(models.Model):
    receipt = models.ForeignKey(
        CustomerReturnReceipt, on_delete=models.PROTECT, related_name="lines"
    )
    line = models.ForeignKey(CustomerReturnLine, on_delete=models.PROTECT, related_name="receipts")
    location = models.ForeignKey("locations.Location", on_delete=models.PROTECT)
    batch_no = models.CharField(max_length=64, blank=True)
    production_date = models.DateField(null=True)
    expiry_date = models.DateField(null=True)
    serial_no = models.CharField(max_length=64, blank=True)
    qty_ok = models.DecimalField(max_digits=18, decimal_places=4)
    qty_damage = models.DecimalField(max_digits=18, decimal_places=4)
    qty_reject = models.DecimalField(max_digits=18, decimal_places=4)
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(qty_ok__gte=0, qty_damage__gte=0, qty_reject__gte=0)
                & (Q(qty_ok__gt=0) | Q(qty_damage__gt=0) | Q(qty_reject__gt=0)),
                name="cr_receipt_positive",
            )
        ]


class CustomerReturnPhoto(models.Model):
    order = models.ForeignKey(CustomerReturn, on_delete=models.PROTECT, related_name="photos")
    line = models.ForeignKey(CustomerReturnLine, on_delete=models.PROTECT, related_name="photos")
    receipt_line = models.ForeignKey(
        CustomerReturnReceiptLine, on_delete=models.PROTECT, null=True, related_name="photos"
    )
    purpose = models.CharField(
        max_length=12, choices=[("APPLICATION", "申请"), ("RECEIPT", "验收"), ("NOTE", "补充")]
    )
    file = models.FileField(storage=private_return_storage, upload_to=return_photo_path)
    content_type = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    sealed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class CustomerReturnEvent(models.Model):
    order = models.ForeignKey(CustomerReturn, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    reason = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["id"]


class CustomerReturnCommand(models.Model):
    """Successful idempotency receipts, serialized under the original-order lock."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    order = models.ForeignKey(CustomerReturn, on_delete=models.PROTECT)
    receipt = models.ForeignKey(CustomerReturnReceipt, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "request_id"], name="cr_command_unique")
        ]
