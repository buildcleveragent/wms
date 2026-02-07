from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from decimal import Decimal, ROUND_HALF_UP
from allapp.billing.enums import ChargeType, CalcMethod, AccrualStatus, PeriodStatus, BillStatus, MetricType, LadderMode, CapMode, BundleScope, BundleType
from wmsmaster import settings

User = get_user_model()

def qmoney(val):
    if val is None: return None
    return (Decimal(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

class BillingRule(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), null=True, blank=True, on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), null=True, blank=True, on_delete=models.PROTECT,default=settings.DEFAULT_WAREHOUSE_ID,editable=False,)
    charge_type = models.CharField(verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices)
    calc_method = models.CharField(verbose_name=_("计量方式"), max_length=40, choices=CalcMethod.choices)
    ladder_mode = models.CharField(verbose_name=_("阶梯模式"), max_length=16, choices=LadderMode.choices, null=True, blank=True, default=None)
    unit_price = models.DecimalField(verbose_name=_("单价/费率（无阶梯时生效）"), max_digits=18, decimal_places=4)
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    taxable = models.BooleanField(verbose_name=_("含税"), default=False)
    tax_rate = models.DecimalField(verbose_name=_("税率"), max_digits=6, decimal_places=4, default=Decimal("0.0000"))
    min_charge = models.DecimalField(verbose_name=_("最低收费"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    # —— 新增：封顶 —— #
    cap_mode = models.CharField(verbose_name=_("封顶口径"), max_length=16, choices=CapMode.choices, null=True, blank=True, default=CapMode.NONE)
    cap_amount = models.DecimalField(verbose_name=_("封顶金额"), max_digits=18, decimal_places=2, null=True, blank=True)
    # —— 新增：打包（分组键/口径/类型/打包价） —— #
    bundle_key = models.CharField(verbose_name=_("打包分组键"), max_length=40, blank=True, default="")
    bundle_scope = models.CharField(verbose_name=_("打包口径"), max_length=16, choices=BundleScope.choices, null=True, blank=True, default=BundleScope.NONE)
    bundle_type = models.CharField(verbose_name=_("打包类型"), max_length=8, choices=BundleType.choices, null=True, blank=True, default=BundleType.CAP)
    bundle_price = models.DecimalField(verbose_name=_("打包价"), max_digits=18, decimal_places=2, null=True, blank=True)
    # —— 通用 —— #
    active = models.BooleanField(verbose_name=_("是否启用"), default=True)
    priority = models.IntegerField(verbose_name=_("优先级（小数优先）"), default=100)
    effective_from = models.DateField(verbose_name=_("生效开始日"), null=True, blank=True)
    effective_to = models.DateField(verbose_name=_("生效截止日"), null=True, blank=True)
    note = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("计费规则")
        verbose_name_plural = _("计费规则")
        indexes = [models.Index(fields=["owner", "charge_type", "active", "priority"])]
        constraints = [
            models.CheckConstraint(name="chk_rule_price_nonneg", check=models.Q(unit_price__gte=0)),
            models.CheckConstraint(name="chk_rule_taxrate_range", check=models.Q(tax_rate__gte=0, tax_rate__lte=1)),
        ]

    def __str__(self):
        scope = f"{self.owner_id or '*'}"
        return f"[{scope}] {self.charge_type}/{self.calc_method} ladder={self.ladder_mode or '-'} cap={self.cap_mode or '-'} bundle={self.bundle_scope or '-'}"

class BillingRuleTier(models.Model):
    rule = models.ForeignKey("billing.BillingRule", verbose_name=_("计费规则"), on_delete=models.CASCADE, related_name="tiers")
    threshold_from = models.DecimalField(verbose_name=_("起始阈值(含)"), max_digits=18, decimal_places=4)
    threshold_to = models.DecimalField(verbose_name=_("截至阈值(不含)"), max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(verbose_name=_("单价(按量)"), max_digits=18, decimal_places=4, null=True, blank=True)
    percent_rate = models.DecimalField(verbose_name=_("费率(按金额)"), max_digits=18, decimal_places=6, null=True, blank=True)
    note = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("阶梯规则")
        verbose_name_plural = _("阶梯规则")
        indexes = [models.Index(fields=["rule", "threshold_from", "threshold_to"])]
        constraints = [
            models.UniqueConstraint(fields=["rule", "threshold_from", "threshold_to"], name="ux_rule_tier_from_to"),
            models.CheckConstraint(name="chk_tier_range_valid", check=models.Q(threshold_to__isnull=True) | models.Q(threshold_to__gt=models.F("threshold_from"))),
            models.CheckConstraint(name="chk_tier_price_or_rate", check=(models.Q(unit_price__isnull=False, percent_rate__isnull=True) | models.Q(unit_price__isnull=True, percent_rate__isnull=False))),
        ]

    def __str__(self):
        rng = f"[{self.threshold_from}, {self.threshold_to or '∞'})"
        tag = f"价{self.unit_price}" if self.unit_price is not None else f"率{self.percent_rate}"
        return f"{self.rule_id} {rng} {tag}"

class BillingPeriod(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT,default=settings.DEFAULT_WAREHOUSE_ID,editable=False,)
    label = models.CharField(verbose_name=_("账期标签"), max_length=20)
    start_date = models.DateField(verbose_name=_("开始日期"))
    end_date = models.DateField(verbose_name=_("结束日期"))
    status = models.CharField(verbose_name=_("账期状态"), max_length=20, choices=PeriodStatus.choices, default=PeriodStatus.OPEN)
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")

    class Meta:
        verbose_name = _("账期")
        verbose_name_plural = _("账期")
        constraints = [models.UniqueConstraint(fields=["owner", "warehouse", "label"], name="ux_billing_period_owner_wh_label")]

    def __str__(self):
        return f"{self.owner_id}-{self.label}({self.status})"

class BillingEvent(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT, editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    charge_type = models.CharField(verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices)
    service_date = models.DateField(verbose_name=_("服务日期"))
    task = models.ForeignKey("tasking.WmsTask", verbose_name=_("来源任务"), null=True, blank=True, on_delete=models.SET_NULL)
    task_line = models.ForeignKey("tasking.WmsTaskLine", verbose_name=_("来源任务行"), null=True, blank=True, on_delete=models.SET_NULL)
    scan_log = models.ForeignKey("tasking.TaskScanLog", verbose_name=_("来源扫描记录"), null=True, blank=True, on_delete=models.SET_NULL)
    posting_journal = models.ForeignKey("inventory.PostingJournal", verbose_name=_("过账日志"), null=True, blank=True, on_delete=models.SET_NULL)
    quantity = models.DecimalField(verbose_name=_("计费数量"), max_digits=18, decimal_places=4, default=Decimal("0"))
    quantity_uom = models.CharField(verbose_name=_("数量单位"), max_length=20, default="BASE")
    event_fp = models.CharField(verbose_name=_("事件指纹"), max_length=120, unique=True)
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)

    class Meta:
        verbose_name = _("计费事件")
        verbose_name_plural = _("计费事件")

class BillingAccrual(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT, editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    period = models.ForeignKey("billing.BillingPeriod", verbose_name=_("账期"), null=True, blank=True, on_delete=models.SET_NULL)
    charge_type = models.CharField(verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices)
    rule = models.ForeignKey("billing.BillingRule", verbose_name=_("计费规则"), on_delete=models.PROTECT)
    service_date = models.DateField(verbose_name=_("服务日期"))
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    quantity = models.DecimalField(verbose_name=_("计费数量"), max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(verbose_name=_("有效单价/费率"), max_digits=18, decimal_places=4)
    amount = models.DecimalField(verbose_name=_("金额(不含税)"), max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(verbose_name=_("税额"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(verbose_name=_("状态"), max_length=20, choices=AccrualStatus.choices, default=AccrualStatus.OPEN)
    event = models.ForeignKey("billing.BillingEvent", verbose_name=_("来源事件"), null=True, blank=True, on_delete=models.SET_NULL)
    # —— 新增：为了打包分组统计 —— #
    bundle_key = models.CharField(verbose_name=_("打包分组键"), max_length=40, blank=True, default="")
    acc_fingerprint = models.CharField(verbose_name=_("应计指纹"), max_length=160, unique=True)
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)
    created_by = models.ForeignKey(User, verbose_name=_("创建人"), null=True, blank=True, on_delete=models.SET_NULL, related_name="billing_created_by")

    class Meta:
        verbose_name = _("应计费用")
        verbose_name_plural = _("应计费用")
        indexes = [
            models.Index(fields=["owner", "service_date", "charge_type", "status"]),
            models.Index(fields=["bundle_key", "service_date"]),  # 便于打包分组
        ]
        constraints = [
            models.CheckConstraint(name="chk_amount_nonneg", check=models.Q(amount__gte=0)),
            models.CheckConstraint(name="chk_qty_nonneg", check=models.Q(quantity__gte=0)),
        ]

class BillingMetricDaily(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"),  editable=False,on_delete=models.PROTECT,default=settings.DEFAULT_WAREHOUSE_ID)
    service_date = models.DateField(verbose_name=_("日期"))
    metric_type = models.CharField(verbose_name=_("指标类型"), max_length=20, choices=MetricType.choices)
    value = models.DecimalField(verbose_name=_("指标值"), max_digits=18, decimal_places=4)
    source = models.CharField(verbose_name=_("来源"), max_length=40, blank=True, default="")
    note = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)

    class Meta:
        verbose_name = _("计费日指标")
        verbose_name_plural = _("计费日指标")
        indexes = [models.Index(fields=["owner", "warehouse", "service_date", "metric_type"])]
        constraints = [models.UniqueConstraint(fields=["owner", "warehouse", "service_date", "metric_type"], name="ux_billing_metric_daily_owh_date_metric")]

class Bill(models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT, editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    period = models.ForeignKey("billing.BillingPeriod", verbose_name=_("账期"), on_delete=models.PROTECT)
    invoice_no = models.CharField(verbose_name=_("发票/结算单号"), max_length=40, unique=True)
    issue_date = models.DateField(verbose_name=_("开票日期"), default=timezone.now)
    due_date = models.DateField(verbose_name=_("到期日期"), null=True, blank=True)
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    subtotal = models.DecimalField(verbose_name=_("小计(不含税)"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(verbose_name=_("税额合计"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(verbose_name=_("价税合计"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(verbose_name=_("单据状态"), max_length=20, choices=BillStatus.choices, default=BillStatus.DRAFT)
    memo = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("发票/结算单")
        verbose_name_plural = _("发票/结算单")

class BillLine(models.Model):
    bill = models.ForeignKey("billing.Bill", verbose_name=_("所属发票/结算单"), on_delete=models.CASCADE, related_name="lines")
    accrual = models.ForeignKey("billing.BillingAccrual", verbose_name=_("来源应计"), on_delete=models.PROTECT)
    charge_type = models.CharField(verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices)
    service_date = models.DateField(verbose_name=_("服务日期"))
    quantity = models.DecimalField(verbose_name=_("计费数量"), max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(verbose_name=_("有效单价/费率"), max_digits=18, decimal_places=4)
    amount = models.DecimalField(verbose_name=_("金额(不含税)"), max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(verbose_name=_("税额"), max_digits=18, decimal_places=2, default=Decimal("0.00"))
    description = models.CharField(verbose_name=_("行说明"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("发票/结算单明细")
        verbose_name_plural = _("发票/结算单明细")
