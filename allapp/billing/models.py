import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from allapp.billing.enums import (
    AccrualStatus,
    BillDocumentStatus,
    BillPaymentStatus,
    BillStatus,
    BundleScope,
    BundleType,
    CalcMethod,
    CapMode,
    ChargeType,
    LadderMode,
    MetricType,
    PaymentReceiptStatus,
    PeriodStatus,
    PricingStatus,
    SourceQuality,
)

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
    return Decimal(start_a) < _decimal_range_end(end_b) and Decimal(start_b) < _decimal_range_end(
        end_a
    )


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
    owner = models.ForeignKey(
        "baseinfo.Owner",
        verbose_name=_("货主"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    warehouse = models.ForeignKey(
        "locations.Warehouse",
        verbose_name=_("大仓"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    charge_type = models.CharField(
        verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices
    )
    calc_method = models.CharField(
        verbose_name=_("计量方式"), max_length=40, choices=CalcMethod.choices
    )
    ladder_mode = models.CharField(
        verbose_name=_("阶梯模式"),
        max_length=16,
        choices=LadderMode.choices,
        null=True,
        blank=True,
        default=None,
    )
    unit_price = models.DecimalField(
        verbose_name=_("单价/费率（无阶梯时生效）"),
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    taxable = models.BooleanField(verbose_name=_("含税"), default=False)
    tax_rate = models.DecimalField(
        verbose_name=_("税率"),
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.0000"),
    )
    min_charge = models.DecimalField(
        verbose_name=_("最低收费"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    # —— 新增：封顶 —— #
    cap_mode = models.CharField(
        verbose_name=_("封顶口径"),
        max_length=16,
        choices=CapMode.choices,
        null=True,
        blank=True,
        default=CapMode.NONE,
    )
    cap_amount = models.DecimalField(
        verbose_name=_("封顶金额"),
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # —— 新增：打包（分组键/口径/类型/打包价） —— #
    bundle_key = models.CharField(
        verbose_name=_("打包分组键"), max_length=40, blank=True, default=""
    )
    bundle_scope = models.CharField(
        verbose_name=_("打包口径"),
        max_length=16,
        choices=BundleScope.choices,
        null=True,
        blank=True,
        default=BundleScope.NONE,
    )
    bundle_type = models.CharField(
        verbose_name=_("打包类型"),
        max_length=8,
        choices=BundleType.choices,
        null=True,
        blank=True,
        default=BundleType.CAP,
    )
    bundle_price = models.DecimalField(
        verbose_name=_("打包价"), max_digits=18, decimal_places=2, null=True, blank=True
    )
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
                fields=[
                    "active",
                    "charge_type",
                    "calc_method",
                    "owner",
                    "warehouse",
                    "priority",
                ],
                name="ix_rule_select",
            )
        ]
        constraints = [
            models.CheckConstraint(
                name="chk_rule_price_nonneg",
                condition=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0),
            ),
            models.CheckConstraint(
                name="chk_rule_taxrate_range",
                condition=models.Q(tax_rate__gte=0, tax_rate__lte=1),
            ),
            models.CheckConstraint(
                name="chk_rule_min_charge_nonneg", condition=models.Q(min_charge__gte=0)
            ),
            models.CheckConstraint(
                name="chk_rule_cap_amount_nonneg",
                condition=models.Q(cap_amount__isnull=True) | models.Q(cap_amount__gte=0),
            ),
            models.CheckConstraint(
                name="chk_rule_bundle_price_nonneg",
                condition=models.Q(bundle_price__isnull=True) | models.Q(bundle_price__gte=0),
            ),
        ]

    def __str__(self):
        scope = f"{self.owner_id or '*'}"
        return (
            f"[{scope}] {self.charge_type}/{self.calc_method} "
            f"ladder={self.ladder_mode or '-'} cap={self.cap_mode or '-'} "
            f"bundle={self.bundle_scope or '-'}"
        )

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
    rule = models.ForeignKey(
        "billing.BillingRule",
        verbose_name=_("计费规则"),
        on_delete=models.CASCADE,
        related_name="tiers",
    )
    threshold_from = models.DecimalField(
        verbose_name=_("起始阈值(含)"), max_digits=18, decimal_places=4
    )
    threshold_to = models.DecimalField(
        verbose_name=_("截至阈值(不含)"),
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    unit_price = models.DecimalField(
        verbose_name=_("单价(按量)"),
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
    )
    percent_rate = models.DecimalField(
        verbose_name=_("费率(按金额)"),
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    note = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("阶梯规则")
        verbose_name_plural = _("阶梯规则")
        indexes = [models.Index(fields=["rule", "threshold_from", "threshold_to"])]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "threshold_from", "threshold_to"],
                name="ux_rule_tier_from_to",
            ),
            models.CheckConstraint(
                name="chk_tier_range_valid",
                condition=models.Q(threshold_to__isnull=True)
                | models.Q(threshold_to__gt=models.F("threshold_from")),
            ),
            models.CheckConstraint(
                name="chk_tier_price_or_rate",
                condition=(
                    models.Q(unit_price__isnull=False, percent_rate__isnull=True)
                    | models.Q(unit_price__isnull=True, percent_rate__isnull=False)
                ),
            ),
            models.CheckConstraint(
                name="chk_tier_from_nonneg", condition=models.Q(threshold_from__gte=0)
            ),
            models.CheckConstraint(
                name="chk_tier_unit_price_nonneg",
                condition=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0),
            ),
            models.CheckConstraint(
                name="chk_tier_percent_rate_rng",
                condition=models.Q(percent_rate__isnull=True)
                | (models.Q(percent_rate__gte=0) & models.Q(percent_rate__lte=1)),
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
                BillingRuleTier.objects.filter(rule_id=self.rule_id)
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


class BillingServiceContract(BillingValidationMixin, models.Model):
    """Declares which operational facts are expected to enter billing.

    Contracts deliberately do not contain prices.  They are the completeness
    boundary used by close-readiness, while BillingRule remains the pricing
    boundary.
    """

    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("仓库"), on_delete=models.PROTECT
    )
    charge_type = models.CharField(
        verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices
    )
    calc_method = models.CharField(
        verbose_name=_("计量方式"), max_length=40, choices=CalcMethod.choices
    )
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    effective_from = models.DateField(verbose_name=_("生效日期"))
    effective_to = models.DateField(verbose_name=_("失效日期"), null=True, blank=True)
    source_type = models.CharField(verbose_name=_("来源类型"), max_length=40, default="TASK")
    billing_timing = models.CharField(
        verbose_name=_("计费时点"), max_length=40, default="ON_POSTING"
    )
    is_active = models.BooleanField(verbose_name=_("启用"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("计费服务合同")
        verbose_name_plural = _("计费服务合同")
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "owner",
                    "warehouse",
                    "charge_type",
                    "calc_method",
                    "currency",
                    "effective_from",
                ],
                name="ux_bill_contract_scope_from",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="chk_bill_contract_dates",
            ),
        ]
        indexes = [
            models.Index(
                fields=["owner", "warehouse", "effective_from", "effective_to"],
                name="ix_bill_contract_scope_dt",
            )
        ]

    def clean(self):
        errors = {}
        if self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "失效日期不能早于生效日期。"
        if all(
            [
                self.owner_id,
                self.warehouse_id,
                self.charge_type,
                self.calc_method,
                self.currency,
                self.effective_from,
            ]
        ):
            overlaps = BillingServiceContract.objects.filter(
                owner_id=self.owner_id,
                warehouse_id=self.warehouse_id,
                charge_type=self.charge_type,
                calc_method=self.calc_method,
                currency=self.currency,
                effective_from__lte=self.effective_to or datetime.date.max,
            ).filter(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=self.effective_from)
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                errors["effective_from"] = "同一服务合同范围的生效日期不能重叠。"
        if errors:
            raise ValidationError(errors)


class BillingPeriodQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if {"owner", "owner_id", "warehouse", "warehouse_id"} & kwargs.keys():
            raise ValidationError("账期创建后禁止修改货主或仓库。")
        protected = {"label", "start_date", "end_date", "currency"} & kwargs.keys()
        if protected and self.exclude(status=PeriodStatus.OPEN).exists():
            raise ValidationError("已关闭或已开票账期的业务字段禁止修改。")
        if "status" in kwargs:
            raise ValidationError("账期状态只能通过 lock/unlock/invoice 领域动作修改。")
        return super().update(**kwargs)

    def delete(self):
        if self.exclude(status=PeriodStatus.OPEN).exists():
            raise ValidationError("已关闭或已开票账期禁止删除。")
        return super().delete()


class BillingPeriod(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    label = models.CharField(verbose_name=_("账期标签"), max_length=20)
    start_date = models.DateField(verbose_name=_("开始日期"))
    end_date = models.DateField(verbose_name=_("结束日期"))
    status = models.CharField(
        verbose_name=_("账期状态"),
        max_length=20,
        choices=PeriodStatus.choices,
        default=PeriodStatus.OPEN,
    )
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    closed_at = models.DateTimeField(verbose_name=_("关账时间"), null=True, blank=True)
    closed_by = models.ForeignKey(
        User,
        verbose_name=_("关账人"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_periods_closed",
    )
    invoiced_at = models.DateTimeField(verbose_name=_("开票时间"), null=True, blank=True)
    invoiced_by = models.ForeignKey(
        User,
        verbose_name=_("开票人"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_periods_invoiced",
    )
    transition_quality = models.CharField(
        verbose_name=_("状态时间质量"), max_length=40, default="VERIFIED"
    )

    objects = BillingPeriodQuerySet.as_manager()

    class Meta:
        verbose_name = _("账期")
        verbose_name_plural = _("账期")
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "warehouse", "label"],
                name="ux_billing_period_owner_wh_label",
            ),
            models.CheckConstraint(
                name="chk_billing_period_date_order",
                condition=models.Q(end_date__gte=models.F("start_date")),
            ),
        ]

    def __str__(self):
        return f"{self.owner_id}-{self.label}({self.status})"

    def clean(self):
        errors = {}
        if self.pk:
            original = BillingPeriod.objects.filter(pk=self.pk).first()
            if original is not None:
                if original.owner_id != self.owner_id:
                    errors["owner"] = "账期创建后禁止修改货主。"
                if original.warehouse_id != self.warehouse_id:
                    errors["warehouse"] = "账期创建后禁止修改仓库。"
                protected_fields = ("label", "start_date", "end_date", "currency")
                if original.status in {PeriodStatus.CLOSED, PeriodStatus.INVOICED}:
                    for field in protected_fields:
                        if getattr(original, field) != getattr(self, field):
                            errors[field] = (
                                "已关闭或已开票账期的业务字段不可修改，请先执行撤销关账。"
                            )
        if self.start_date and self.end_date and self.start_date > self.end_date:
            errors["end_date"] = "账期结束日期不能早于开始日期。"

        if self.owner_id and self.warehouse_id and self.start_date and self.end_date:
            overlap = (
                BillingPeriod.objects.filter(
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

    def delete(self, *args, **kwargs):
        if self.status != PeriodStatus.OPEN:
            raise ValidationError("已关闭或已开票账期禁止删除。")
        if self.billingaccrual_set.exists() or self.bill_set.exists():
            raise ValidationError("存在应计或账单关联的账期禁止删除。")
        return super().delete(*args, **kwargs)


class BillingEvent(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    charge_type = models.CharField(
        verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices
    )
    calc_method = models.CharField(
        verbose_name=_("计量方式"),
        max_length=40,
        choices=CalcMethod.choices,
        null=True,
        blank=True,
    )
    service_date = models.DateField(verbose_name=_("服务日期"))
    task = models.ForeignKey(
        "tasking.WmsTask",
        verbose_name=_("来源任务"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    task_line = models.ForeignKey(
        "tasking.WmsTaskLine",
        verbose_name=_("来源任务行"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    scan_log = models.ForeignKey(
        "tasking.TaskScanLog",
        verbose_name=_("来源扫描记录"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    posting_journal = models.ForeignKey(
        "inventory.PostingJournal",
        verbose_name=_("过账日志"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    metric = models.ForeignKey(
        "billing.BillingMetricDaily",
        verbose_name=_("来源计费指标"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_events",
    )
    pricing_rule = models.ForeignKey(
        "billing.BillingRule",
        verbose_name=_("实际计价规则"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="priced_events",
    )
    bundle_key = models.CharField(
        verbose_name=_("打包分组键"), max_length=40, blank=True, default=""
    )
    quantity = models.DecimalField(
        verbose_name=_("计费数量"),
        max_digits=18,
        decimal_places=4,
        default=Decimal("0"),
    )
    quantity_uom = models.CharField(verbose_name=_("数量单位"), max_length=20, default="BASE")
    pricing_status = models.CharField(
        verbose_name=_("计价状态"),
        max_length=20,
        choices=PricingStatus.choices,
        default=PricingStatus.PENDING,
        db_index=True,
    )
    pricing_reason = models.CharField(
        verbose_name=_("计价原因"), max_length=80, blank=True, default=""
    )
    pricing_detail = models.JSONField(verbose_name=_("计价明细"), blank=True, default=dict)
    priced_at = models.DateTimeField(verbose_name=_("计价完成时间"), null=True, blank=True)
    event_fp = models.CharField(verbose_name=_("事件指纹"), max_length=120, unique=True)
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)

    class Meta:
        verbose_name = _("计费事件")
        verbose_name_plural = _("计费事件")
        indexes = [
            models.Index(
                fields=["owner", "warehouse", "service_date", "charge_type"],
                name="ix_bevt_scope_dt",
            ),
            models.Index(
                fields=["owner", "warehouse", "service_date", "pricing_status"],
                name="ix_bevt_scope_price",
            ),
        ]

    def clean(self):
        errors = {}

        if self.task_id:
            if self.task.owner_id != self.owner_id or self.task.warehouse_id != self.warehouse_id:
                errors["task"] = "task 的 owner/warehouse 必须与计费事件一致。"

        if self.task_line_id:
            task_line_task = self.task_line.task
            if (
                task_line_task.owner_id != self.owner_id
                or task_line_task.warehouse_id != self.warehouse_id
            ):
                errors["task_line"] = "task_line 的 owner/warehouse 必须与计费事件一致。"
            if self.task_id and self.task_line.task_id != self.task_id:
                errors["task_line"] = "task_line 必须属于当前 task。"

        if self.scan_log_id:
            if (
                self.scan_log.owner_id != self.owner_id
                or self.scan_log.warehouse_id != self.warehouse_id
            ):
                errors["scan_log"] = "scan_log 的 owner/warehouse 必须与计费事件一致。"
            if self.task_id and self.scan_log.task_id != self.task_id:
                errors["scan_log"] = "scan_log 必须属于当前 task。"
            if self.task_line_id and self.scan_log.task_line_id != self.task_line_id:
                errors["scan_log"] = "scan_log 必须属于当前 task_line。"

        if self.posting_journal_id:
            expected_task_id = (
                self.task_id
                or getattr(self.task_line, "task_id", None)
                or getattr(self.scan_log, "task_id", None)
            )
            if not expected_task_id:
                errors["posting_journal"] = (
                    "设置 posting_journal 时，必须同时关联 task、task_line 或 scan_log。"
                )
            elif expected_task_id:
                if (
                    self.posting_journal.src_model != "WmsTask"
                    or self.posting_journal.src_id != expected_task_id
                ):
                    errors["posting_journal"] = "posting_journal 必须与当前事件关联的 task 一致。"

        if self.metric_id:
            if (
                self.metric.owner_id != self.owner_id
                or self.metric.warehouse_id != self.warehouse_id
            ):
                errors["metric"] = "metric 的 owner/warehouse 必须与计费事件一致。"
            if self.metric.service_date != self.service_date:
                errors["metric"] = "metric 的服务日期必须与计费事件一致。"

        if self.pricing_status == PricingStatus.UNPRICED and not self.pricing_reason:
            errors["pricing_reason"] = "未定价事件必须记录原因。"
        if self.pricing_status == PricingStatus.NO_CHARGE:
            if not self.pricing_rule_id:
                errors["pricing_rule"] = "无需收费事件必须记录匹配规则。"
            if not self.pricing_reason:
                errors["pricing_reason"] = "无需收费事件必须记录原因。"
            if not self.pricing_detail:
                errors["pricing_detail"] = "无需收费事件必须记录计价明细。"
        if (
            self.pricing_status
            in {PricingStatus.ACCRUED, PricingStatus.NO_CHARGE, PricingStatus.UNPRICED}
            and not self.priced_at
        ):
            errors["priced_at"] = "终态计价事件必须记录完成时间。"

        if errors:
            raise ValidationError(errors)


class BillingAccrual(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    period = models.ForeignKey(
        "billing.BillingPeriod",
        verbose_name=_("账期"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    charge_type = models.CharField(
        verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices
    )
    rule = models.ForeignKey(
        "billing.BillingRule", verbose_name=_("计费规则"), on_delete=models.PROTECT
    )
    service_date = models.DateField(verbose_name=_("服务日期"))
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    quantity = models.DecimalField(verbose_name=_("计费数量"), max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(
        verbose_name=_("有效单价/费率"), max_digits=18, decimal_places=4
    )
    amount = models.DecimalField(verbose_name=_("金额(不含税)"), max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(
        verbose_name=_("税额"), max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    status = models.CharField(
        verbose_name=_("状态"),
        max_length=20,
        choices=AccrualStatus.choices,
        default=AccrualStatus.OPEN,
    )
    event = models.ForeignKey(
        "billing.BillingEvent",
        verbose_name=_("来源事件"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # —— 新增：为了打包分组统计 —— #
    bundle_key = models.CharField(
        verbose_name=_("打包分组键"), max_length=40, blank=True, default=""
    )
    acc_fingerprint = models.CharField(verbose_name=_("应计指纹"), max_length=160, unique=True)
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        verbose_name=_("创建人"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_created_by",
    )
    source_quality = models.CharField(
        verbose_name=_("来源质量"),
        max_length=20,
        choices=SourceQuality.choices,
        default=SourceQuality.VERIFIED,
        db_index=True,
    )
    source_note = models.CharField(
        verbose_name=_("来源质量说明"), max_length=200, blank=True, default=""
    )
    # —— 撤销/红冲支持 —— #
    pre_adjustment_amount = models.DecimalField(
        verbose_name=_("调整前金额"),
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_reversal = models.BooleanField(verbose_name=_("是否冲销"), default=False)
    reversal_of = models.ForeignKey(
        "self",
        verbose_name=_("冲销来源"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversals",
    )

    class Meta:
        verbose_name = _("应计费用")
        verbose_name_plural = _("应计费用")
        indexes = [
            models.Index(fields=["owner", "service_date", "charge_type", "status"]),
            models.Index(
                fields=["owner", "warehouse", "service_date", "charge_type", "status"],
                name="ix_accr_owh_dt_ct_st",
            ),
            models.Index(fields=["bundle_key", "service_date"]),  # 便于打包分组
        ]
        constraints = [
            models.CheckConstraint(name="chk_qty_nonneg", condition=models.Q(quantity__gte=0)),
            models.CheckConstraint(
                name="chk_reversal_has_ref",
                condition=models.Q(is_reversal=False) | models.Q(reversal_of__isnull=False),
            ),
            models.CheckConstraint(
                name="chk_locked_accrual_has_period",
                condition=~models.Q(status=AccrualStatus.LOCKED) | models.Q(period__isnull=False),
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
            if (
                self.period.owner_id != self.owner_id
                or self.period.warehouse_id != self.warehouse_id
            ):
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

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        if self.event_id and self.status != AccrualStatus.VOID:
            BillingEvent.objects.filter(pk=self.event_id).update(
                pricing_status=PricingStatus.ACCRUED,
                pricing_rule_id=self.rule_id,
                pricing_reason="PRICED",
                priced_at=timezone.now(),
            )
        return result


class BillingMetricDaily(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    service_date = models.DateField(verbose_name=_("日期"))
    metric_type = models.CharField(
        verbose_name=_("指标类型"), max_length=20, choices=MetricType.choices
    )
    value = models.DecimalField(verbose_name=_("指标值"), max_digits=18, decimal_places=4)
    source = models.CharField(verbose_name=_("来源"), max_length=40, blank=True, default="")
    source_quality = models.CharField(
        verbose_name=_("来源质量"),
        max_length=20,
        choices=SourceQuality.choices,
        default=SourceQuality.VERIFIED,
        db_index=True,
    )
    note = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")
    created_at = models.DateTimeField(verbose_name=_("创建时间"), auto_now_add=True)

    class Meta:
        verbose_name = _("计费日指标")
        verbose_name_plural = _("计费日指标")
        indexes = [models.Index(fields=["owner", "warehouse", "service_date", "metric_type"])]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "warehouse", "service_date", "metric_type"],
                name="ux_billing_metric_daily_owh_date_metric",
            ),
            models.CheckConstraint(
                name="chk_metric_value_nonneg", condition=models.Q(value__gte=0)
            ),
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
        WARNING = "WARNING", "有风险"
        SKIPPED = "SKIPPED", "跳过"

    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    job_name = models.CharField(verbose_name=_("作业名"), max_length=40, choices=JobName.choices)
    service_date = models.DateField(verbose_name=_("服务日期"))
    status = models.CharField(
        verbose_name=_("执行状态"),
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
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
        if (
            self.status
            in {
                self.Status.SUCCESS,
                self.Status.FAILED,
                self.Status.WARNING,
                self.Status.SKIPPED,
            }
            and self.finished_at is None
        ):
            errors["finished_at"] = "终态作业必须填写 finished_at。"
        if self.status == self.Status.RUNNING and self.finished_at is not None:
            errors["finished_at"] = "RUNNING 状态下 finished_at 必须为空。"
        if errors:
            raise ValidationError(errors)


class Bill(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name=_("货主"), on_delete=models.PROTECT)
    warehouse = models.ForeignKey(
        "locations.Warehouse", verbose_name=_("大仓"), on_delete=models.PROTECT
    )
    period = models.ForeignKey(
        "billing.BillingPeriod", verbose_name=_("账期"), on_delete=models.PROTECT
    )
    invoice_no = models.CharField(verbose_name=_("发票/结算单号"), max_length=40, unique=True)
    issue_date = models.DateField(verbose_name=_("开票日期"), default=bill_issue_date_default)
    due_date = models.DateField(verbose_name=_("到期日期"), null=True, blank=True)
    currency = models.CharField(verbose_name=_("币种"), max_length=8, default="CNY")
    subtotal = models.DecimalField(
        verbose_name=_("小计(不含税)"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    tax_total = models.DecimalField(
        verbose_name=_("税额合计"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total = models.DecimalField(
        verbose_name=_("价税合计"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(
        verbose_name=_("单据状态"),
        max_length=20,
        choices=BillStatus.choices,
        default=BillStatus.DRAFT,
    )
    document_status = models.CharField(
        verbose_name=_("单据状态(新)"),
        max_length=20,
        choices=BillDocumentStatus.choices,
        default=BillDocumentStatus.DRAFT,
        db_index=True,
    )
    payment_status = models.CharField(
        verbose_name=_("回款状态"),
        max_length=20,
        choices=BillPaymentStatus.choices,
        default=BillPaymentStatus.UNPAID,
        db_index=True,
    )
    memo = models.CharField(verbose_name=_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("发票/结算单")
        verbose_name_plural = _("发票/结算单")
        indexes = [
            models.Index(fields=["owner", "warehouse", "status"], name="ix_bill_owh_stat"),
        ]

    def clean(self):
        errors = {}
        if self.period_id:
            if (
                self.period.owner_id != self.owner_id
                or self.period.warehouse_id != self.warehouse_id
            ):
                errors["period"] = "period 的 owner/warehouse 必须与 bill 一致。"
            if self.currency and self.period.currency and self.currency != self.period.currency:
                errors["currency"] = "bill.currency 必须与 period.currency 一致。"
            if self.status != BillStatus.VOID:
                duplicate_bill = Bill.objects.filter(
                    owner_id=self.owner_id,
                    warehouse_id=self.warehouse_id,
                    period_id=self.period_id,
                ).exclude(status=BillStatus.VOID)
                if self.pk:
                    duplicate_bill = duplicate_bill.exclude(pk=self.pk)
                if duplicate_bill.exists():
                    errors["period"] = "该账期已存在有效发票/结算单。"
        if self.issue_date and self.due_date and self.due_date < self.issue_date:
            errors["due_date"] = "due_date 不能早于 issue_date。"
        if self.subtotal is not None and self.subtotal < 0:
            errors["subtotal"] = "subtotal 不能为负数。"
        if self.tax_total is not None and self.tax_total < 0:
            errors["tax_total"] = "tax_total 不能为负数。"
        if self.total is not None and self.total < 0:
            errors["total"] = "total 不能为负数。"
        if self.total is not None and self.subtotal is not None and self.tax_total is not None:
            expected_total = qmoney(Decimal(self.subtotal) + Decimal(self.tax_total))
            if qmoney(self.total) != expected_total:
                errors["total"] = "total 必须等于 subtotal + tax_total。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.status == BillStatus.DRAFT:
            self.document_status = BillDocumentStatus.DRAFT
            self.payment_status = BillPaymentStatus.UNPAID
        elif self.status == BillStatus.VOID:
            self.document_status = BillDocumentStatus.VOID
        elif self.status in {BillStatus.ISSUED, BillStatus.PAID}:
            self.document_status = BillDocumentStatus.ISSUED
            if self.status == BillStatus.PAID:
                self.payment_status = BillPaymentStatus.PAID
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "document_status",
                "payment_status",
            }
        return super().save(*args, **kwargs)

    @property
    def paid_amount(self):
        posted = Decimal("0.00")
        for allocation in self.payment_allocations.all():
            if allocation.receipt.status not in {
                PaymentReceiptStatus.POSTED,
                PaymentReceiptStatus.REVERSED,
            }:
                continue
            posted += -allocation.amount if allocation.is_reversal else allocation.amount
        return qmoney(posted)

    @property
    def outstanding_amount(self):
        return qmoney(max(Decimal("0.00"), Decimal(self.total) - self.paid_amount))

    @property
    def paid_at(self):
        """Latest verified allocation date once the bill is fully paid."""

        if self.payment_status != BillPaymentStatus.PAID:
            return None
        verified = [
            allocation.receipt.receipt_date
            for allocation in self.payment_allocations.all()
            if not allocation.is_reversal
            and allocation.receipt.status
            in {PaymentReceiptStatus.POSTED, PaymentReceiptStatus.REVERSED}
            and allocation.receipt.date_quality == "VERIFIED"
        ]
        return max(verified, default=None)


class BillLine(BillingValidationMixin, models.Model):
    bill = models.ForeignKey(
        "billing.Bill",
        verbose_name=_("所属发票/结算单"),
        on_delete=models.CASCADE,
        related_name="lines",
    )
    accrual = models.ForeignKey(
        "billing.BillingAccrual", verbose_name=_("来源应计"), on_delete=models.PROTECT
    )
    charge_type = models.CharField(
        verbose_name=_("计费类型"), max_length=20, choices=ChargeType.choices
    )
    service_date = models.DateField(verbose_name=_("服务日期"))
    quantity = models.DecimalField(verbose_name=_("计费数量"), max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(
        verbose_name=_("有效单价/费率"), max_digits=18, decimal_places=4
    )
    amount = models.DecimalField(verbose_name=_("金额(不含税)"), max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(
        verbose_name=_("税额"), max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    description = models.CharField(verbose_name=_("行说明"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = _("发票/结算单明细")
        verbose_name_plural = _("发票/结算单明细")
        constraints = [
            models.UniqueConstraint(fields=["accrual"], name="ux_billline_accrual_once"),
            models.CheckConstraint(
                name="chk_billline_qty_nonneg", condition=models.Q(quantity__gte=0)
            ),
        ]

    def clean(self):
        errors = {}

        if self.bill_id and self.accrual_id:
            if (
                self.bill.owner_id != self.accrual.owner_id
                or self.bill.warehouse_id != self.accrual.warehouse_id
            ):
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


class PaymentReceiptQuerySet(models.QuerySet):
    def delete(self):
        if self.exclude(status=PaymentReceiptStatus.DRAFT).exists():
            raise ValidationError("已过账或已冲销收款单禁止删除。")
        return super().delete()

    def update(self, **kwargs):
        business_fields = {
            "owner",
            "owner_id",
            "warehouse",
            "warehouse_id",
            "currency",
            "receipt_no",
            "receipt_date",
            "date_quality",
            "amount",
            "channel",
            "bank_reference",
            "memo",
        }
        if (
            business_fields.intersection(kwargs)
            and self.exclude(status=PaymentReceiptStatus.DRAFT).exists()
        ):
            raise ValidationError("已过账或已冲销收款单的业务字段不可修改。")
        return super().update(**kwargs)


class PaymentReceipt(BillingValidationMixin, models.Model):
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT)
    currency = models.CharField(max_length=8)
    receipt_no = models.CharField(max_length=40, unique=True)
    receipt_date = models.DateField()
    date_quality = models.CharField(max_length=20, default="VERIFIED")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    channel = models.CharField(max_length=40, blank=True, default="")
    bank_reference = models.CharField(max_length=80, blank=True, default="")
    memo = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=PaymentReceiptStatus.choices,
        default=PaymentReceiptStatus.DRAFT,
        db_index=True,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posted_receipts",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reversed_receipts",
    )
    reversal_of = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal_receipt",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_receipts",
    )
    objects = PaymentReceiptQuerySet.as_manager()

    class Meta:
        verbose_name = _("收款单")
        verbose_name_plural = _("收款单")
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="chk_receipt_amount_pos")
        ]
        indexes = [
            models.Index(
                fields=["owner", "warehouse", "currency", "receipt_date"],
                name="ix_receipt_scope_date",
            )
        ]

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "收款金额必须大于零。"
        if self.reversal_of_id and self.reversal_of.reversal_of_id:
            errors["reversal_of"] = "冲销单不能再次作为冲销来源。"
        if self.pk:
            original = PaymentReceipt.objects.filter(pk=self.pk).first()
            if original and original.status != PaymentReceiptStatus.DRAFT:
                immutable = (
                    "owner_id",
                    "warehouse_id",
                    "currency",
                    "receipt_no",
                    "receipt_date",
                    "date_quality",
                    "amount",
                    "channel",
                    "bank_reference",
                    "memo",
                    "reversal_of_id",
                )
                if any(getattr(original, field) != getattr(self, field) for field in immutable):
                    errors["status"] = "已过账或已冲销收款单的业务字段不可修改。"
                if (
                    not (
                        original.status == PaymentReceiptStatus.POSTED
                        and self.status
                        in {PaymentReceiptStatus.POSTED, PaymentReceiptStatus.REVERSED}
                    )
                    and self.status != original.status
                ):
                    errors["status"] = "非法收款单状态迁移。"
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        if self.status != PaymentReceiptStatus.DRAFT:
            raise ValidationError("已过账或已冲销收款单禁止删除。")
        return super().delete(*args, **kwargs)


class PaymentAllocationQuerySet(models.QuerySet):
    def delete(self):
        if self.exclude(receipt__status=PaymentReceiptStatus.DRAFT).exists():
            raise ValidationError("已过账核销分录禁止删除。")
        return super().delete()

    def update(self, **kwargs):
        if self.exclude(receipt__status=PaymentReceiptStatus.DRAFT).exists():
            raise ValidationError("已过账核销分录不可修改。")
        return super().update(**kwargs)


class PaymentAllocation(BillingValidationMixin, models.Model):
    receipt = models.ForeignKey(
        PaymentReceipt, on_delete=models.PROTECT, related_name="allocations"
    )
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    allocated_at = models.DateTimeField(default=timezone.now)
    is_reversal = models.BooleanField(default=False)
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    objects = PaymentAllocationQuerySet.as_manager()

    class Meta:
        verbose_name = _("收款核销分录")
        verbose_name_plural = _("收款核销分录")
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="chk_alloc_amount_pos"),
            models.UniqueConstraint(
                fields=["receipt", "bill", "is_reversal"],
                name="ux_alloc_receipt_bill_side",
            ),
        ]
        indexes = [models.Index(fields=["bill", "allocated_at"], name="ix_alloc_bill_time")]

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "核销金额必须大于零。"
        if self.receipt_id and self.bill_id:
            if (
                self.receipt.owner_id != self.bill.owner_id
                or self.receipt.warehouse_id != self.bill.warehouse_id
                or self.receipt.currency != self.bill.currency
            ):
                errors["bill"] = "收款单与账单必须属于同一货主、仓库和币种。"
        if self.is_reversal and not self.reversal_of_id:
            errors["reversal_of"] = "反向核销必须指向原核销分录。"
        if self.pk:
            original = (
                PaymentAllocation.objects.filter(pk=self.pk).select_related("receipt").first()
            )
            if original and original.receipt.status != PaymentReceiptStatus.DRAFT:
                errors["receipt"] = "已过账核销分录不可修改。"
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        if self.receipt.status != PaymentReceiptStatus.DRAFT:
            raise ValidationError("已过账核销分录禁止删除。")
        return super().delete(*args, **kwargs)


class ReceivableCollectionCase(BillingValidationMixin, models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "待跟进"
        IN_PROGRESS = "IN_PROGRESS", "跟进中"
        PROMISED = "PROMISED", "已承诺"
        RESOLVED = "RESOLVED", "已解决"
        CLOSED = "CLOSED", "已关闭"

    bill = models.OneToOneField(Bill, on_delete=models.PROTECT, related_name="collection_case")
    assignee = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="collection_cases",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    promised_payment_date = models.DateField(null=True, blank=True)
    promised_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("manage_collections", "维护应收催收记录")]


class CollectionActivityQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("催收活动是追加式审计记录，禁止修改。")

    def delete(self):
        raise ValidationError("催收活动是追加式审计记录，禁止删除。")


class CollectionActivity(BillingValidationMixin, models.Model):
    case = models.ForeignKey(
        ReceivableCollectionCase, on_delete=models.PROTECT, related_name="activities"
    )
    contacted_at = models.DateTimeField(default=timezone.now)
    channel = models.CharField(max_length=40)
    result = models.CharField(max_length=80)
    note = models.TextField(blank=True, default="")
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    objects = CollectionActivityQuerySet.as_manager()

    class Meta:
        ordering = ["contacted_at", "id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("催收活动是追加式审计记录，禁止修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("催收活动是追加式审计记录，禁止删除。")
