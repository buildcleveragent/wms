from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from decimal import Decimal, ROUND_HALF_UP
from allapp.billing.enums import ChargeType, CalcMethod, AccrualStatus, PeriodStatus, BillStatus, MetricType, LadderMode, CapMode, BundleScope, BundleType

User = get_user_model()

def qmoney(val):
    if val is None:
        return None
    return (Decimal(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def bill_issue_date_default():
    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


def _decimal_range_end(value):
    return Decimal("Infinity") if value is None else Decimal(value)


def _decimal_ranges_overlap(start_a, end_a, start_b, end_b) -> bool:
    return Decimal(start_a) < _decimal_range_end(end_b) and Decimal(start_b) < _decimal_range_end(end_a)


class BillingValidationMixin(models.Model):
    """
    Save-time guardrail for normal ORM writes.

    Note: QuerySet.update(), bulk_create(), and bulk_update() bypass this hook.
    """
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class BillingRule(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), null=True, blank=True, on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), null=True, blank=True, on_delete=models.PROTECT)
    charge_type = models.CharField(verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices)
    calc_method = models.CharField(verbose_name=_("计量方式"), max_length=40, choices=CalcMethod.choices)
    ladder_mode = models.CharField(verbose_name=_("阶梯模式"), max_length=16, choices=LadderMode.choices, null=True, blank=True, default=None)
    unit_price = models.DecimalField(verbose_name=_("单价/费率（无阶梯时生效）"), max_digits=18, decimal_places=4, null=True, blank=True)
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
        indexes = [
            models.Index(
                fields=["active", "charge_type", "calc_method", "owner", "warehouse", "priority"],
                name="ix_rule_select",
            )
        ]
        constraints = [
            models.CheckConstraint(name="chk_rule_price_nonneg", check=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0)),
            models.CheckConstraint(name="chk_rule_taxrate_range", check=models.Q(tax_rate__gte=0, tax_rate__lte=1)),
            models.CheckConstraint(name="chk_rule_min_charge_nonneg", check=models.Q(min_charge__gte=0)),
            models.CheckConstraint(
                name="chk_rule_cap_amount_nonneg",
                check=models.Q(cap_amount__isnull=True) | models.Q(cap_amount__gte=0),
            ),
            models.CheckConstraint(
                name="chk_rule_bundle_price_nonneg",
                check=models.Q(bundle_price__isnull=True) | models.Q(bundle_price__gte=0),
            ),
        ]

    def __str__(self):
        scope = f"{self.owner_id or '*'}"
        return f"[{scope}] {self.charge_type}/{self.calc_method} ladder={self.ladder_mode or '-'} cap={self.cap_mode or '-'} bundle={self.bundle_scope or '-'}"

    def clean(self):
        errors = {}
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            errors["effective_to"] = "生效截止日不能早于生效开始日。"
        if self.cap_mode in {None, "", CapMode.NONE}:
            if self.cap_amount is not None:
                errors["cap_amount"] = "cap_mode 为 NONE 时，cap_amount 必须为空。"
        elif self.cap_amount is None:
            errors["cap_amount"] = "启用封顶时必须填写 cap_amount。"

        if self.bundle_scope in {None, "", BundleScope.NONE}:
            if self.bundle_key:
                errors["bundle_key"] = "bundle_scope 为 NONE 时，bundle_key 必须为空。"
            if self.bundle_price is not None:
                errors["bundle_price"] = "bundle_scope 为 NONE 时，bundle_price 必须为空。"
        else:
            if not self.bundle_key:
                errors["bundle_key"] = "启用打包时必须填写 bundle_key。"
            if self.bundle_price is None:
                errors["bundle_price"] = "启用打包时必须填写 bundle_price。"

        has_tiers = self.pk and self.tiers.exists() if self.pk else False
        if self.unit_price is None and not has_tiers and self.ladder_mode is None:
            errors["unit_price"] = "非阶梯模式下必须填写 unit_price。"
        elif self.ladder_mode is not None and not has_tiers and self.unit_price is None:
            errors["unit_price"] = "启用阶梯模式但尚无阶梯配置时，必须填写 unit_price 作为兜底。"

        if self.calc_method == CalcMethod.PERCENT_OF_ORDER_AMOUNT and self.unit_price is not None:
            if not (Decimal("0") <= Decimal(self.unit_price) <= Decimal("1")):
                errors["unit_price"] = "按订单金额比例计费时，unit_price 必须在 0~1 之间。"

        if errors:
            raise ValidationError(errors)


class BillingRuleTier(BillingValidationMixin, models.Model):
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
            models.CheckConstraint(name="chk_tier_from_nonneg", check=models.Q(threshold_from__gte=0)),
            models.CheckConstraint(
                name="chk_tier_unit_price_nonneg",
                check=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0),
            ),
            models.CheckConstraint(
                name="chk_tier_percent_rate_rng",
                check=models.Q(percent_rate__isnull=True) | (models.Q(percent_rate__gte=0) & models.Q(percent_rate__lte=1)),
            ),
        ]

    def __str__(self):
        rng = f"[{self.threshold_from}, {self.threshold_to or '∞'})"
        tag = f"价{self.unit_price}" if self.unit_price is not None else f"率{self.percent_rate}"
        return f"{self.rule_id} {rng} {tag}"

    def clean(self):
        errors = {}
        if self.threshold_from is not None and self.threshold_from < 0:
            errors["threshold_from"] = "起始阈值不能为负数。"
        if self.threshold_to is not None and self.threshold_to <= self.threshold_from:
            errors["threshold_to"] = "截至阈值必须大于起始阈值。"
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "unit_price 不能为负数。"
        if self.percent_rate is not None:
            if self.percent_rate < 0:
                errors["percent_rate"] = "percent_rate 不能为负数。"
            elif self.percent_rate > 1:
                errors["percent_rate"] = "percent_rate 必须在 0~1 之间。"

        has_unit_price = self.unit_price is not None
        has_percent_rate = self.percent_rate is not None
        if has_unit_price == has_percent_rate:
            errors["percent_rate"] = "unit_price 和 percent_rate 必须二选一。"

        if self.rule_id:
            if self.rule.calc_method == CalcMethod.PERCENT_OF_ORDER_AMOUNT:
                if self.unit_price is not None:
                    errors["unit_price"] = "按订单金额比例的阶梯规则只允许填写 percent_rate。"
            elif self.percent_rate is not None:
                errors["percent_rate"] = "当前计量方式的阶梯规则只允许填写 unit_price。"

        if self.rule_id and self.threshold_from is not None:
            overlapping = (
                BillingRuleTier.objects
                .filter(rule_id=self.rule_id)
                .exclude(pk=self.pk)
                .only("id", "threshold_from", "threshold_to")
            )
            for tier in overlapping:
                if _decimal_ranges_overlap(
                    self.threshold_from,
                    self.threshold_to,
                    tier.threshold_from,
                    tier.threshold_to,
                ):
                    errors["threshold_from"] = "同一规则下的阶梯区间不能重叠。"
                    break

        if errors:
            raise ValidationError(errors)


class BillingPeriod(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
    label = models.CharField(verbose_name=_("账期标签"), max_length=20)
    start_date = models.DateField(verbose_name=_("开始日期"))
    end_date = models.DateField(verbose_name=_("结束日期"))
    status = models.CharField(verbose_name=_("账期状态"), max_length=20, choices=PeriodStatus.choices, default=PeriodStatus.OPEN)
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")

    class Meta:
        verbose_name = _("账期")
        verbose_name_plural = _("账期")
        constraints = [
            models.UniqueConstraint(fields=["owner", "warehouse", "label"], name="ux_billing_period_owner_wh_label"),
            models.CheckConstraint(
                name="chk_billing_period_date_order",
                condition=models.Q(end_date__gte=models.F("start_date")),
            ),
        ]

    def __str__(self):
        return f"{self.owner_id}-{self.label}({self.status})"

    def clean(self):
        errors = {}
        if self.start_date and self.end_date and self.start_date > self.end_date:
            errors["end_date"] = "账期结束日期不能早于开始日期。"

        if self.owner_id and self.warehouse_id and self.start_date and self.end_date:
            overlap = (
                BillingPeriod.objects
                .filter(
                    owner_id=self.owner_id,
                    warehouse_id=self.warehouse_id,
                    start_date__lte=self.end_date,
                    end_date__gte=self.start_date,
                )
                .exclude(pk=self.pk)
                .only("id", "label")
                .first()
            )
            if overlap:
                errors["start_date"] = f"账期不能重叠，冲突账期: {overlap.label}。"

        if errors:
            raise ValidationError(errors)


class BillingEvent(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
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
        indexes = [
            models.Index(fields=["owner", "warehouse", "service_date", "charge_type"], name="ix_bevt_scope_dt"),
        ]

    def clean(self):
        errors = {}

        if self.task_id:
            if self.task.owner_id != self.owner_id or self.task.warehouse_id != self.warehouse_id:
                errors["task"] = "task 的 owner/warehouse 必须与计费事件一致。"

        if self.task_line_id:
            task_line_task = self.task_line.task
            if task_line_task.owner_id != self.owner_id or task_line_task.warehouse_id != self.warehouse_id:
                errors["task_line"] = "task_line 的 owner/warehouse 必须与计费事件一致。"
            if self.task_id and self.task_line.task_id != self.task_id:
                errors["task_line"] = "task_line 必须属于当前 task。"

        if self.scan_log_id:
            if self.scan_log.owner_id != self.owner_id or self.scan_log.warehouse_id != self.warehouse_id:
                errors["scan_log"] = "scan_log 的 owner/warehouse 必须与计费事件一致。"
            if self.task_id and self.scan_log.task_id != self.task_id:
                errors["scan_log"] = "scan_log 必须属于当前 task。"
            if self.task_line_id and self.scan_log.task_line_id != self.task_line_id:
                errors["scan_log"] = "scan_log 必须属于当前 task_line。"

        if self.posting_journal_id:
            expected_task_id = self.task_id or getattr(self.task_line, "task_id", None) or getattr(self.scan_log, "task_id", None)
            if not expected_task_id:
                errors["posting_journal"] = "设置 posting_journal 时，必须同时关联 task、task_line 或 scan_log。"
            elif expected_task_id:
                if self.posting_journal.src_model != "WmsTask" or self.posting_journal.src_id != expected_task_id:
                    errors["posting_journal"] = "posting_journal 必须与当前事件关联的 task 一致。"

        if errors:
            raise ValidationError(errors)


class BillingAccrual(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
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
    # —— 撤销/红冲支持 —— #
    pre_adjustment_amount = models.DecimalField(verbose_name=_("调整前金额"), max_digits=18, decimal_places=2, null=True, blank=True)
    is_reversal = models.BooleanField(verbose_name=_("是否冲销"), default=False)
    reversal_of = models.ForeignKey("self", verbose_name=_("冲销来源"), null=True, blank=True, on_delete=models.PROTECT, related_name="reversals")

    class Meta:
        verbose_name = _("应计费用")
        verbose_name_plural = _("应计费用")
        indexes = [
            models.Index(fields=["owner", "service_date", "charge_type", "status"]),
            models.Index(fields=["owner", "warehouse", "service_date", "charge_type", "status"], name="ix_accr_owh_dt_ct_st"),
            models.Index(fields=["bundle_key", "service_date"]),  # 便于打包分组
        ]
        constraints = [
            models.CheckConstraint(name="chk_qty_nonneg", check=models.Q(quantity__gte=0)),
            models.CheckConstraint(
                name="chk_reversal_has_ref",
                check=models.Q(is_reversal=False) | models.Q(reversal_of__isnull=False),
            ),
        ]

    def clean(self):
        errors = {}
        currency_errors = []

        if not self.is_reversal:
            if self.amount is not None and self.amount < 0:
                errors["amount"] = "非冲销记录金额不能为负。"
            if self.unit_price is not None and self.unit_price < 0:
                errors["unit_price"] = "非冲销记录单价不能为负。"
            if self.tax_amount is not None and self.tax_amount < 0:
                errors["tax_amount"] = "非冲销记录税额不能为负。"

        if self.rule_id:
            if self.rule.owner_id is not None and self.rule.owner_id != self.owner_id:
                errors["rule"] = "rule.owner 必须与 accrual.owner 一致。"
            if self.rule.warehouse_id is not None and self.rule.warehouse_id != self.warehouse_id:
                errors["rule"] = "rule.warehouse 必须与 accrual.warehouse 一致。"
            if self.rule.charge_type != self.charge_type:
                errors["charge_type"] = "charge_type 必须与 rule.charge_type 一致。"
            if self.rule.currency and self.currency and self.rule.currency != self.currency:
                currency_errors.append("currency 必须与 rule.currency 一致。")

        if self.period_id:
            if self.period.owner_id != self.owner_id or self.period.warehouse_id != self.warehouse_id:
                errors["period"] = "period 的 owner/warehouse 必须与 accrual 一致。"
            if not (self.period.start_date <= self.service_date <= self.period.end_date):
                errors["service_date"] = "service_date 必须落在 period 区间内。"
            if self.period.currency and self.currency and self.period.currency != self.currency:
                currency_errors.append("currency 必须与 period.currency 一致。")

        if self.event_id:
            if self.event.owner_id != self.owner_id or self.event.warehouse_id != self.warehouse_id:
                errors["event"] = "event 的 owner/warehouse 必须与 accrual 一致。"
            if self.event.charge_type != self.charge_type and "charge_type" not in errors:
                errors["charge_type"] = "charge_type 必须与 event.charge_type 一致。"
            if self.event.service_date != self.service_date:
                errors["service_date"] = "service_date 必须与 event.service_date 一致。"

        if currency_errors:
            errors["currency"] = currency_errors

        if errors:
            raise ValidationError(errors)


class BillingMetricDaily(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
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
        constraints = [
            models.UniqueConstraint(fields=["owner", "warehouse", "service_date", "metric_type"], name="ux_billing_metric_daily_owh_date_metric"),
            models.CheckConstraint(name="chk_metric_value_nonneg", check=models.Q(value__gte=0)),
        ]

    def clean(self):
        errors = {}
        if self.value is not None and self.value < 0:
            errors["value"] = "计费指标值不能为负数。"
        if errors:
            raise ValidationError(errors)

class BillingJobRun(BillingValidationMixin, models.Model):
    class JobName(models.TextChoices):
        DAILY_METRIC_GENERATION = "DAILY_METRIC_GENERATION", "日指标生成"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "运行中"
        SUCCESS = "SUCCESS", "成功"
        FAILED = "FAILED", "失败"
        SKIPPED = "SKIPPED", "跳过"

    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
    job_name = models.CharField(verbose_name=_("作业名"), max_length=40, choices=JobName.choices)
    service_date = models.DateField(verbose_name=_("服务日期"))
    status = models.CharField(verbose_name=_("执行状态"), max_length=20, choices=Status.choices, default=Status.RUNNING)
    attempts = models.PositiveIntegerField(verbose_name=_("尝试次数"), default=1)
    started_at = models.DateTimeField(verbose_name=_("开始时间"), null=True, blank=True)
    finished_at = models.DateTimeField(verbose_name=_("结束时间"), null=True, blank=True)
    message = models.CharField(verbose_name=_("执行消息"), max_length=200, blank=True, default="")
    summary = models.JSONField(verbose_name=_("执行摘要"), blank=True, default=dict)
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)
    updated_at = models.DateTimeField(verbose_name=_("更新时间"), auto_now=True)

    class Meta:
        verbose_name = _("计费作业执行记录")
        verbose_name_plural = _("计费作业执行记录")
        indexes = [
            models.Index(fields=["job_name", "status", "service_date"]),
            models.Index(fields=["owner", "warehouse", "service_date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["job_name", "owner", "warehouse", "service_date"],
                name="ux_billing_job_run_job_owh_date",
            )
        ]

    def clean(self):
        errors = {}
        if self.attempts is not None and self.attempts < 1:
            errors["attempts"] = "attempts 必须大于等于 1。"
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            errors["finished_at"] = "finished_at 不能早于 started_at。"
        if self.status in {self.Status.SUCCESS, self.Status.FAILED, self.Status.SKIPPED} and self.finished_at is None:
            errors["finished_at"] = "终态作业必须填写 finished_at。"
        if self.status == self.Status.RUNNING and self.finished_at is not None:
            errors["finished_at"] = "RUNNING 状态下 finished_at 必须为空。"
        if errors:
            raise ValidationError(errors)

class Bill(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT)
    period = models.ForeignKey("billing.BillingPeriod", verbose_name=_("账期"), on_delete=models.PROTECT)
    invoice_no = models.CharField(verbose_name=_("发票/结算单号"), max_length=40, unique=True)
    issue_date = models.DateField(verbose_name=_("开票日期"), default=bill_issue_date_default)
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
        indexes = [
            models.Index(fields=["owner", "warehouse", "status"], name="ix_bill_owh_stat"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["owner", "warehouse", "period"], name="ux_bill_owner_wh_period"),
        ]

    def clean(self):
        errors = {}
        if self.period_id:
            if self.period.owner_id != self.owner_id or self.period.warehouse_id != self.warehouse_id:
                errors["period"] = "period 的 owner/warehouse 必须与 bill 一致。"
            if self.currency and self.period.currency and self.currency != self.period.currency:
                errors["currency"] = "bill.currency 必须与 period.currency 一致。"
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            errors["due_date"] = "due_date 不能早于 issue_date。"
        if self.total is not None and self.subtotal is not None and self.tax_total is not None:
            expected_total = qmoney(Decimal(self.subtotal) + Decimal(self.tax_total))
            if qmoney(self.total) != expected_total:
                errors["total"] = "total 必须等于 subtotal + tax_total。"
        if errors:
            raise ValidationError(errors)


class BillLine(BillingValidationMixin, models.Model):
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
        constraints = [
            models.UniqueConstraint(fields=["accrual"], name="ux_billline_accrual_once"),
            models.CheckConstraint(name="chk_billline_qty_nonneg", condition=models.Q(quantity__gte=0)),
        ]

    def clean(self):
        errors = {}

        if self.bill_id and self.accrual_id:
            if self.bill.owner_id != self.accrual.owner_id or self.bill.warehouse_id != self.accrual.warehouse_id:
                errors["accrual"] = "accrual 的 owner/warehouse 必须与 bill 一致。"
            if self.bill.period_id != self.accrual.period_id:
                errors["accrual"] = "accrual 必须属于当前 bill.period。"

        if self.accrual_id:
            if self.charge_type != self.accrual.charge_type:
                errors["charge_type"] = "charge_type 必须与 accrual.charge_type 一致。"
            if self.service_date != self.accrual.service_date:
                errors["service_date"] = "service_date 必须与 accrual.service_date 一致。"
            if self.quantity != self.accrual.quantity:
                errors["quantity"] = "quantity 必须与 accrual.quantity 一致。"
            if self.unit_price != self.accrual.unit_price:
                errors["unit_price"] = "unit_price 必须与 accrual.unit_price 一致。"
            if self.amount != self.accrual.amount:
                errors["amount"] = "amount 必须与 accrual.amount 一致。"
            if self.tax_amount != self.accrual.tax_amount:
                errors["tax_amount"] = "tax_amount 必须与 accrual.tax_amount 一致。"

        if errors:
            raise ValidationError(errors)
