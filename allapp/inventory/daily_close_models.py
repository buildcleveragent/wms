from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class InventoryClosePilot(models.Model):
    warehouse = models.OneToOneField("locations.Warehouse", on_delete=models.PROTECT)
    start_date = models.DateField("启用业务日")
    enabled = models.BooleanField("启用", default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class InventoryCloseScope(models.Model):
    pilot = models.ForeignKey(InventoryClosePilot, on_delete=models.PROTECT)
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT)
    start_date = models.DateField()
    through_date = models.DateField(null=True)
    scanned_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pilot", "owner"], name="inventory_close_scope")
        ]


class InventoryDailyClose(models.Model):
    class Quality(models.TextChoices):
        TRUSTED = "TRUSTED", "可信"
        APPROXIMATE = "APPROXIMATE", "近似"
        MISSING = "MISSING", "缺失"

    class ReviewState(models.TextChoices):
        NONE = "NONE", "无需复核"
        WAREHOUSE = "WAREHOUSE", "待仓管复核"
        FINANCE = "FINANCE", "待财务签核"
        CLOSED = "CLOSED", "已签核"

    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT)
    business_date = models.DateField()
    quality = models.CharField(max_length=16, choices=Quality.choices, default=Quality.MISSING)
    sealed_at = models.DateTimeField(null=True)
    checked_at = models.DateTimeField(null=True)
    row_count = models.PositiveIntegerField(default=0)
    totals = models.JSONField(default=dict)
    details = models.JSONField(default=list)
    issues = models.JSONField(default=list)
    review_state = models.CharField(
        max_length=16, choices=ReviewState.choices, default=ReviewState.NONE
    )
    case = models.OneToOneField(
        "reports.AlertCase",
        on_delete=models.PROTECT,
        related_name="inventory_daily_close",
        null=True,
    )
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "warehouse", "business_date"], name="inventory_close_day"
            )
        ]
        indexes = [
            models.Index(fields=["warehouse", "business_date"], name="inventory_close_wh_day")
        ]
        permissions = [
            ("review_inventorydailyclose", "仓管复核库存日结"),
            ("sign_inventorydailyclose", "财务签核库存日结"),
        ]


class _EvidenceQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("日结审计凭据不可删除。")

    def update(self, **kwargs):
        raise ValidationError("日结审计凭据不可修改。")


class _Evidence(models.Model):
    objects = _EvidenceQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        raise ValidationError("日结审计凭据不可删除。")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("日结审计凭据不可修改。")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)


class InventoryOpeningVersion(models.Model):
    objects = _EvidenceQuerySet.as_manager()

    def delete(self, *args, **kwargs):
        raise ValidationError("日结期初版本不可删除。")

    def save(self, *args, **kwargs):
        if self.status == self.Status.APPROVED and (
            not self.approved_at
            or not self.approved_by_id
            or self.approved_by_id == self.submitted_by_id
        ):
            raise ValidationError("已签核期初需要另一名签核人及签核时间。")
        if self._state.adding:
            kwargs["force_insert"] = True
            return super().save(*args, **kwargs)
        fields = set(kwargs.get("update_fields") or ())
        if "status" not in fields or not fields.issubset(
            {"status", "approved_by", "approved_at", "decided_at"}
        ):
            raise ValidationError("日结期初版本内容不可改写。")
        if self.status not in (self.Status.APPROVED, self.Status.RETURNED):
            raise ValidationError("期初版本只可签核或退回。")
        # A single guarded UPDATE prevents racing decisions outside the service row lock.
        values = {field: getattr(self, field) for field in fields}
        if (
            not type(self)
            ._base_manager.filter(pk=self.pk, status=self.Status.PENDING)
            .update(**values)
        ):
            raise ValidationError("此版本已签核或退回。")

    class Status(models.TextChoices):
        PENDING = "PENDING", "待签核"
        APPROVED = "APPROVED", "已签核"
        RETURNED = "RETURNED", "已退回"

    close = models.ForeignKey(
        InventoryDailyClose, related_name="opening_versions", on_delete=models.PROTECT
    )
    version = models.PositiveIntegerField()
    rows = models.JSONField(default=list)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="+", on_delete=models.PROTECT, null=True
    )
    approved_at = models.DateTimeField(null=True)
    decided_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["close", "version"], name="inventory_opening_version")
        ]


class InventoryCloseReview(_Evidence):
    close = models.ForeignKey(InventoryDailyClose, related_name="reviews", on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=48)
    note = models.TextField(blank=True)
    references = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class InventoryCloseAttachment(_Evidence):
    review = models.ForeignKey(
        InventoryCloseReview, related_name="attachments", on_delete=models.PROTECT
    )
    storage_key = models.CharField(max_length=255, unique=True)
    file_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
