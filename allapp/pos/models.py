from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models


class PosSale(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "已完成"
        VOIDED = "VOIDED", "已撤销"

    sale_no = models.CharField("POS销售单号", max_length=100, unique=True)
    src_bill_no = models.CharField(
        "小票号/外部单号", max_length=100, blank=True, default="", db_index=True
    )
    idempotency_key = models.CharField(
        "幂等键", max_length=100, unique=True, null=True, blank=True
    )
    idempotency_fingerprint = models.CharField(
        "幂等请求指纹", max_length=64, blank=True, default=""
    )
    warehouse = models.ForeignKey(
        "locations.Warehouse", on_delete=models.PROTECT, related_name="pos_sales"
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_sales",
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        "PosShift",
        on_delete=models.PROTECT,
        related_name="sales",
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
    total_amount = models.DecimalField(
        "应收金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
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
            models.Index(
                fields=["warehouse", "created_at"], name="idx_pos_sale_wh_created"
            ),
            models.Index(fields=["status", "created_at"], name="idx_pos_sale_status"),
            models.Index(fields=["shift", "created_at"], name="idx_pos_sale_shift"),
        ]

    def __str__(self) -> str:
        return self.sale_no


class PosShift(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "进行中"
        CLOSED = "CLOSED", "已交班"
        REOPENED = "REOPENED", "已重开"

    shift_no = models.CharField("班次号", max_length=100, unique=True)
    warehouse = models.ForeignKey(
        "locations.Warehouse", on_delete=models.PROTECT, related_name="pos_shifts"
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_shifts"
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    opened_at = models.DateTimeField("开班时间")
    closed_at = models.DateTimeField("交班时间", null=True, blank=True)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="opened_pos_shifts",
        null=True,
        blank=True,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="closed_pos_shifts",
        null=True,
        blank=True,
    )
    reopened_at = models.DateTimeField("重开时间", null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reopened_pos_shifts",
        null=True,
        blank=True,
    )
    reopen_reason = models.CharField("重开原因", max_length=200, blank=True, default="")
    reopen_count = models.PositiveIntegerField("重开次数", default=0)
    opening_cash_amount = models.DecimalField(
        "备用金", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    expected_cash_amount = models.DecimalField(
        "现金应点金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    actual_cash_amount = models.DecimalField(
        "现金实点金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    cash_difference = models.DecimalField(
        "现金差异", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total_sales_amount = models.DecimalField(
        "净销售额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total_voided_amount = models.DecimalField(
        "作废金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    total_return_amount = models.DecimalField(
        "退货金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    sale_count = models.PositiveIntegerField("销售单数", default=0)
    completed_count = models.PositiveIntegerField("完成单数", default=0)
    voided_count = models.PositiveIntegerField("作废单数", default=0)
    return_count = models.PositiveIntegerField("退货单数", default=0)
    remark = models.CharField("备注", max_length=200, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS班次"
        verbose_name_plural = "POS班次"
        indexes = [
            models.Index(
                fields=["warehouse", "cashier", "status"],
                name="idx_pos_shift_wh_cashier",
            ),
            models.Index(fields=["opened_at"], name="idx_pos_shift_opened"),
        ]

    def __str__(self) -> str:
        return self.shift_no


class PosShiftPaymentSummary(models.Model):
    shift = models.ForeignKey(
        PosShift, on_delete=models.PROTECT, related_name="payment_summaries"
    )
    method = models.CharField(
        "支付方式",
        max_length=20,
        choices=[
            ("CASH", "现金"),
            ("WECHAT", "微信"),
            ("ALIPAY", "支付宝"),
            ("BANK_CARD", "银行卡"),
            ("OTHER", "其他"),
        ],
    )
    sale_count = models.PositiveIntegerField("销售单数", default=0)
    refund_count = models.PositiveIntegerField("退款单数", default=0)
    expected_amount = models.DecimalField(
        "系统金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    refund_amount = models.DecimalField(
        "退款金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    actual_amount = models.DecimalField(
        "实点金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    difference = models.DecimalField(
        "差异", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS班次支付汇总"
        verbose_name_plural = "POS班次支付汇总"
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "method"], name="ux_pos_shift_payment_method"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.shift.shift_no}-{self.method}"


class PosSaleLine(models.Model):
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="lines")
    owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, related_name="pos_sale_lines"
    )
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, related_name="pos_sale_lines"
    )
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

    sale = models.OneToOneField(
        PosSale, on_delete=models.PROTECT, related_name="payment"
    )
    method = models.CharField("支付方式", max_length=20, choices=Method.choices)
    amount_due = models.DecimalField("应收金额", max_digits=18, decimal_places=2)
    amount_received = models.DecimalField("实收金额", max_digits=18, decimal_places=2)
    change_amount = models.DecimalField(
        "找零", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    reference_no = models.CharField(
        "支付参考号", max_length=100, blank=True, default=""
    )
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


class PosPaymentLine(models.Model):
    sale = models.ForeignKey(
        PosSale, on_delete=models.PROTECT, related_name="payment_lines"
    )
    method = models.CharField(
        "支付方式", max_length=20, choices=PosPayment.Method.choices
    )
    amount = models.DecimalField("抵扣金额", max_digits=18, decimal_places=2)
    amount_received = models.DecimalField("实收金额", max_digits=18, decimal_places=2)
    change_amount = models.DecimalField(
        "找零", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    reference_no = models.CharField(
        "支付参考号", max_length=100, blank=True, default=""
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=PosPayment.Status.choices,
        default=PosPayment.Status.PAID,
        db_index=True,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS收款明细"
        verbose_name_plural = "POS收款明细"
        indexes = [
            models.Index(fields=["sale", "method"], name="idx_pos_payline_sale"),
            models.Index(
                fields=["method", "created_at"], name="idx_pos_payline_method"
            ),
            models.Index(
                fields=["status", "created_at"], name="idx_pos_payline_status"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_no}-{self.method}-{self.amount}"


class PosReturn(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "已完成"
        VOIDED = "VOIDED", "已撤销"

    return_no = models.CharField("POS退货单号", max_length=100, unique=True)
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="returns")
    warehouse = models.ForeignKey(
        "locations.Warehouse", on_delete=models.PROTECT, related_name="pos_returns"
    )
    shift = models.ForeignKey(
        PosShift, on_delete=models.PROTECT, related_name="returns"
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_returns",
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
    total_amount = models.DecimalField(
        "退货金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    reason = models.CharField("退货原因", max_length=200, blank=True, default="")
    idempotency_key = models.CharField(
        "幂等键", max_length=100, unique=True, null=True, blank=True
    )
    idempotency_fingerprint = models.CharField(
        "幂等请求指纹", max_length=64, blank=True, default=""
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS退货单"
        verbose_name_plural = "POS退货单"
        indexes = [
            models.Index(
                fields=["warehouse", "created_at"], name="idx_pos_return_wh_created"
            ),
            models.Index(fields=["shift", "created_at"], name="idx_pos_return_shift"),
            models.Index(fields=["sale", "created_at"], name="idx_pos_return_sale"),
        ]

    def __str__(self) -> str:
        return self.return_no


class PosReturnLine(models.Model):
    return_order = models.ForeignKey(
        PosReturn, on_delete=models.PROTECT, related_name="lines"
    )
    sale_line = models.ForeignKey(
        PosSaleLine, on_delete=models.PROTECT, related_name="return_lines"
    )
    owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, related_name="pos_return_lines"
    )
    product = models.ForeignKey(
        "products.Product", on_delete=models.PROTECT, related_name="pos_return_lines"
    )
    line_no = models.PositiveIntegerField("行号")
    qty = models.DecimalField("基本数量", max_digits=18, decimal_places=3)
    price = models.DecimalField("基本单价", max_digits=18, decimal_places=4)
    amount = models.DecimalField("金额", max_digits=18, decimal_places=2)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "POS退货明细"
        verbose_name_plural = "POS退货明细"
        constraints = [
            models.UniqueConstraint(
                fields=["return_order", "line_no"], name="ux_pos_return_line_no"
            ),
        ]
        indexes = [
            models.Index(
                fields=["return_order", "owner"], name="idx_pos_return_line_owner"
            ),
            models.Index(fields=["sale_line"], name="idx_pos_return_line_sale_line"),
            models.Index(fields=["product"], name="idx_pos_return_line_product"),
        ]

    def __str__(self) -> str:
        return f"{self.return_order.return_no}-{self.line_no}"


class PosRefund(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "待退款"
        REFUNDED = "REFUNDED", "已退款"
        FAILED = "FAILED", "退款失败"
        CANCELED = "CANCELED", "已取消"

    return_order = models.ForeignKey(
        PosReturn, on_delete=models.PROTECT, related_name="refunds"
    )
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="refunds")
    shift = models.ForeignKey(
        PosShift, on_delete=models.PROTECT, related_name="refunds"
    )
    method = models.CharField(
        "退款方式", max_length=20, choices=PosPayment.Method.choices
    )
    amount = models.DecimalField("退款金额", max_digits=18, decimal_places=2)
    reference_no = models.CharField(
        "退款参考号", max_length=100, blank=True, default=""
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=Status.choices,
        default=Status.REFUNDED,
        db_index=True,
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processed_pos_refunds",
        null=True,
        blank=True,
    )
    processed_at = models.DateTimeField("退款时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "POS退款流水"
        verbose_name_plural = "POS退款流水"
        indexes = [
            models.Index(
                fields=["return_order", "method"], name="idx_pos_refund_return"
            ),
            models.Index(fields=["shift", "created_at"], name="idx_pos_refund_shift"),
            models.Index(fields=["method", "created_at"], name="idx_pos_refund_method"),
            models.Index(fields=["status", "created_at"], name="idx_pos_refund_status"),
        ]

    def __str__(self) -> str:
        return f"{self.return_order.return_no}-{self.method}-{self.amount}"


class PosSaleOrder(models.Model):
    sale = models.ForeignKey(
        PosSale, on_delete=models.PROTECT, related_name="sale_orders"
    )
    owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, related_name="pos_sale_orders"
    )
    outbound_order = models.OneToOneField(
        "outbound.OutboundOrder",
        on_delete=models.PROTECT,
        related_name="pos_sale_order",
    )
    amount = models.DecimalField(
        "订单金额", max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "POS销售出库关联"
        verbose_name_plural = "POS销售出库关联"
        constraints = [
            models.UniqueConstraint(
                fields=["sale", "owner"], name="ux_pos_sale_owner_order"
            ),
        ]
        indexes = [
            models.Index(fields=["sale", "owner"], name="idx_pos_sale_order_owner"),
        ]

    def __str__(self) -> str:
        return f"{self.sale.sale_no}->{self.outbound_order_id}"


class PosPrintLog(models.Model):
    class PrintType(models.TextChoices):
        RECEIPT = "RECEIPT", "销售小票"
        SHIFT_SUMMARY = "SHIFT_SUMMARY", "交班单"
        POS_STATS = "POS_STATS", "POS统计"

    class Source(models.TextChoices):
        FRONTEND_HTML = "FRONTEND_HTML", "前端HTML"
        BACKEND_HTML = "BACKEND_HTML", "后端HTML"

    sale = models.ForeignKey(
        PosSale,
        on_delete=models.PROTECT,
        related_name="print_logs",
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        PosShift,
        on_delete=models.PROTECT,
        related_name="print_logs",
        null=True,
        blank=True,
    )
    print_type = models.CharField("打印类型", max_length=30, choices=PrintType.choices)
    source = models.CharField(
        "打印来源",
        max_length=30,
        choices=Source.choices,
        default=Source.BACKEND_HTML,
        db_index=True,
    )
    printed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_print_logs",
        null=True,
        blank=True,
    )
    printed_at = models.DateTimeField("打印时间", auto_now_add=True)
    copy_no = models.PositiveIntegerField("打印次数", default=1)
    payload_hash = models.CharField("内容指纹", max_length=64, blank=True, default="")
    remark = models.CharField("备注", max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "POS打印日志"
        verbose_name_plural = "POS打印日志"
        indexes = [
            models.Index(fields=["sale", "print_type"], name="idx_pos_print_sale"),
            models.Index(fields=["shift", "print_type"], name="idx_pos_print_shift"),
        ]

    def __str__(self) -> str:
        target = self.sale_id or self.shift_id or "-"
        return f"{self.print_type}-{target}-{self.copy_no}"


class PosAuditLog(models.Model):
    class Action(models.TextChoices):
        CHECKOUT = "CHECKOUT", "收银结账"
        VOID = "VOID", "销售作废"
        RETURN = "RETURN", "退货"
        REFUND = "REFUND", "退款"
        SHIFT_OPEN = "SHIFT_OPEN", "开班"
        SHIFT_CLOSE = "SHIFT_CLOSE", "交班"
        SHIFT_REOPEN = "SHIFT_REOPEN", "重开班次"
        PRINT = "PRINT", "打印"

    action = models.CharField("动作", max_length=30, choices=Action.choices)
    sale = models.ForeignKey(
        PosSale,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    return_order = models.ForeignKey(
        PosReturn,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        PosShift,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_audit_logs",
        null=True,
        blank=True,
    )
    reason = models.CharField("原因", max_length=200, blank=True, default="")
    metadata = models.JSONField("元数据", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "POS操作审计"
        verbose_name_plural = "POS操作审计"
        indexes = [
            models.Index(fields=["action", "created_at"], name="idx_pos_audit_action"),
            models.Index(fields=["sale", "created_at"], name="idx_pos_audit_sale"),
            models.Index(fields=["shift", "created_at"], name="idx_pos_audit_shift"),
            models.Index(
                fields=["return_order", "created_at"], name="idx_pos_audit_return"
            ),
        ]

    def __str__(self) -> str:
        target = self.sale_id or self.return_order_id or self.shift_id or "-"
        return f"{self.action}-{target}"
