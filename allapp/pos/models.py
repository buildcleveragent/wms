from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class PosSale(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "已完成"
        VOIDED = "VOIDED", "已撤销"

    sale_no = models.CharField("POS销售单号", max_length=100, unique=True)
    src_bill_no = models.CharField("小票号/外部单号", max_length=100, blank=True, default="", db_index=True)
    idempotency_key = models.CharField("幂等键", max_length=100, unique=True, null=True, blank=True)
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, related_name="pos_sales")
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_sales",
        null=True,
        blank=True,
    )
    selected_customer = models.ForeignKey(
        "baseinfo.Customer",
        on_delete=models.PROTECT,
        related_name="pos_sales",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_index=True,
    )
    total_amount = models.DecimalField("应收金额", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    remark = models.CharField("备注", max_length=200, blank=True, default="")
    voided_at = models.DateTimeField("撤销时间", null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="voided_pos_sales",
        null=True,
        blank=True,
    )
    void_reason = models.CharField("撤销原因", max_length=200, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS销售单"
        verbose_name_plural = "POS销售单"
        indexes = [
            models.Index(fields=["warehouse", "created_at"], name="idx_pos_sale_wh_created"),
            models.Index(fields=["status", "created_at"], name="idx_pos_sale_status"),
        ]

    def __str__(self) -> str:
        return self.sale_no


class PosSaleLine(models.Model):
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="lines")
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, related_name="pos_sale_lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="pos_sale_lines")
    outbound_order_line = models.OneToOneField(
        "outbound.OutboundOrderLine",
        on_delete=models.PROTECT,
        related_name="pos_sale_line",
        null=True,
        blank=True,
    )
    line_no = models.PositiveIntegerField("行号")
    qty = models.DecimalField("基本数量", max_digits=18, decimal_places=3)
    price = models.DecimalField("基本单价", max_digits=18, decimal_places=4)
    amount = models.DecimalField("金额", max_digits=18, decimal_places=2)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "POS销售明细"
        verbose_name_plural = "POS销售明细"
        constraints = [
            models.UniqueConstraint(fields=["sale", "line_no"], name="ux_pos_line_no"),
        ]
        indexes = [
            models.Index(fields=["sale", "owner"], name="idx_pos_line_sale_owner"),
            models.Index(fields=["product"], name="idx_pos_line_product"),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_no}-{self.line_no}"


class PosPayment(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "现金"
        WECHAT = "WECHAT", "微信"
        ALIPAY = "ALIPAY", "支付宝"
        BANK_CARD = "BANK_CARD", "银行卡"
        OTHER = "OTHER", "其他"

    class Status(models.TextChoices):
        PAID = "PAID", "已收款"
        VOIDED = "VOIDED", "已撤销"

    sale = models.OneToOneField(PosSale, on_delete=models.PROTECT, related_name="payment")
    method = models.CharField("支付方式", max_length=20, choices=Method.choices)
    amount_due = models.DecimalField("应收金额", max_digits=18, decimal_places=2)
    amount_received = models.DecimalField("实收金额", max_digits=18, decimal_places=2)
    change_amount = models.DecimalField("找零", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    reference_no = models.CharField("支付参考号", max_length=100, blank=True, default="")
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.PAID,
        db_index=True,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS收款"
        verbose_name_plural = "POS收款"
        indexes = [
            models.Index(fields=["method", "created_at"], name="idx_pos_pay_method"),
            models.Index(fields=["status", "created_at"], name="idx_pos_pay_status"),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_no}-{self.method}"


class PosSaleOrder(models.Model):
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="sale_orders")
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, related_name="pos_sale_orders")
    outbound_order = models.OneToOneField(
        "outbound.OutboundOrder",
        on_delete=models.PROTECT,
        related_name="pos_sale_order",
    )
    amount = models.DecimalField("订单金额", max_digits=18, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "POS销售出库关联"
        verbose_name_plural = "POS销售出库关联"
        constraints = [
            models.UniqueConstraint(fields=["sale", "owner"], name="ux_pos_sale_owner_order"),
        ]
        indexes = [
            models.Index(fields=["sale", "owner"], name="idx_pos_sale_order_owner"),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_no}->{self.outbound_order_id}"
