from django.utils import timezone
from allapp.outbound.enums import PricingStatus
from decimal import Decimal
from django.core.exceptions import ValidationError
from datetime import date
from allapp.outbound import services as ob_services
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, F, Max
from django.contrib.contenttypes.fields import GenericForeignKey
from decimal import Decimal
from django.db import transaction,models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

from allapp.products.models import Product, ProductPackage, ProductUom
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.baseinfo.models import Supplier
from allapp.core.models import BaseModel, DocSequence
from allapp.outbound.enums import PricingStatus

# ===================== 订单:出库订单 =====================
class OutboundOrder(BaseModel):
    SUBMIT_CHOICES = [
        ("SUBMITTED", "已提交"), ("DRAFT", "未提交")
    ]
    APPROVAL_CHOICES = [
        ("OWNER_PENDING", "待货主管理员审核"),
        ("OWNER_APPROVED", "货主管理员已审核通过"),
        ("OWNER_REJECTED", "货主管理员已驳回"),
        ("WHS_PENDING", "待仓库管理员确认"),
        ("WHS_APPROVED", "仓库管理员已确认,待拣货"),
        ("WHS_REJECTED", "仓库管理员已驳回"),
        ("CANCELLED", "已取消"),
    ]
    DELIVERY_METHOD_CHOICES = [
        ("PICKUP", "客户自提"),
        ("OWN_TRUCK", "配送"),
        ("COURIER", "快递/小包"),  # 顺丰、京东、邮政等
    ]
    OUTBOUND_TYPE_CHOICES = [
        ("SALES", "销售出库"),
        ("TRANSFER", "调拨出库"),
        ("OTHER_OUT", "其他出库"),
        ("SUPPLIER_RETURN", "退回供应商"),
    ]



    created_at = models.DateTimeField("制表时间", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="制表人",
        on_delete=models.PROTECT,
        blank=True, null=True,
        related_name="%(class)s_created",
    )

    # 当 outbound_type=SUPPLIER_RETURN 才会用到 supplier；否则用 customer
    supplier = models.ForeignKey(
        "baseinfo.Supplier", on_delete=models.PROTECT, null=True, blank=True,
        related_name="outbound_returns", verbose_name="供应商"
    )

    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主", on_delete=models.PROTECT, related_name="outbound_orders")
    customer = models.ForeignKey("baseinfo.Customer", verbose_name="客户", on_delete=models.PROTECT, related_name="outbound_orders", blank=True, null=True)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="仓库", on_delete=models.PROTECT, related_name="outbound_orders")

    order_no = models.CharField("订单编号", max_length=100, unique=True)  # 去掉多余 db_index=True
    biz_date = models.DateField("日期", default=date.today)
    src_bill_no = models.CharField("源单号", max_length=100, blank=True, null=True)
    outbound_type = models.CharField("出库类型", max_length=15, choices=OUTBOUND_TYPE_CHOICES, default="SALES")
    delivery_method = models.CharField("交货方式", max_length=15, choices=DELIVERY_METHOD_CHOICES, blank=True, null=True)
    etd = models.DateTimeField("预计发货时间", blank=True, null=True)

    submit_status = models.CharField("提交状态", max_length=15, choices=SUBMIT_CHOICES, default="DRAFT", db_index=True)
    approval_status = models.CharField("审核状态", max_length=15, choices=APPROVAL_CHOICES, default="OWNER_PENDING", db_index=True)

    ship_to = models.CharField("收货地址", max_length=200, blank=True, null=True)
    contact = models.CharField("联系人", max_length=80, blank=True, null=True)
    contact_phone = models.CharField("联系电话", max_length=40, blank=True, null=True)

    memo = models.CharField("备注", max_length=100, blank=True, default="")
    is_closed = models.BooleanField("订单关闭", default=False)
    close_reason = models.CharField("关闭理由", max_length=50, blank=True, null=True)

    approved_by_ownermanager = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # ✅ 统一用户模型
        verbose_name="货主管理员审核人",
        on_delete=models.PROTECT, blank=True, null=True,
        related_name="appr_by_owner_out_ord",
    )
    approved_at_ownermanager = models.DateTimeField("货主管理员审核时间", blank=True, null=True)

    approved_by_warehouse = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # ✅ 统一用户模型
        verbose_name="仓库管理员审核人",
        on_delete=models.PROTECT, blank=True, null=True,
        related_name="appr_by_wh_out_orders",
    )
    approved_at_warehouse = models.DateTimeField("仓库管理员审核时间", blank=True, null=True)

    next_line_no = models.PositiveIntegerField("下一个订单行号", default=10)

    # 在 OutboundOrder 模型中新增下面 4 个字段
    pricing_status = models.CharField(
        "价格状态",
        max_length=20,
        choices=PricingStatus.choices,
        default=PricingStatus.PENDING,
        db_index=True,
    )

    priced_at = models.DateTimeField("价格确认时间", null=True, blank=True)

    priced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="价格确认人",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="priced_outbound_orders",
    )

    final_order_amount = models.DecimalField(
        "最终订单金额",
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    # 业务语义约定：
    # - Product.price：参考价 / 建议价
    # - OutboundOrderLine.base_price：最终成交的基本单位单价
    # - OutboundOrder.final_order_amount：冻结后的订单总金额

    class Meta:
        verbose_name = "出库订单"
        verbose_name_plural = "出库订单"
        permissions = [
            ("approve_outbound_as_owner_manager", "货主管理员可执行出库审核动作"),
            ("approve_outbound_as_wh_manager",   "仓库管理员可执行出库确认动作"),
            ("submit_outbound_as_owner_buyers", "货主业务员可提交出库订单"),
        ]
        ordering = ["-biz_date", "-id"]
        indexes = [
            models.Index(fields=["owner", "biz_date", "submit_status"], name="idx_out_owner_date_stat"),
            models.Index(fields=["approval_status"], name="idx_out_appr_stat"),
            models.Index(fields=["customer", "biz_date"], name="idx_out_cust_date"),
            # 可按需加：
            # models.Index(fields=["outbound_type", "biz_date"], name="idx_out_type_date"),
            # models.Index(fields=["supplier", "biz_date"], name="idx_out_supp_date"),
        ]
        constraints = [
           models.CheckConstraint(
            name="ck_o_submit_valid",
            check=Q(submit_status__in=["DRAFT", "SUBMITTED"]),
           ),
           models.CheckConstraint(
            name="ox_valid_approval",
            check=Q(approval_status__in=[
                "OWNER_PENDING","OWNER_APPROVED","OWNER_REJECTED",
                "WHS_PENDING","WHS_APPROVED","WHS_REJECTED","CANCELLED"]),
            ),
            # 已关闭订单不能是已取消
            models.CheckConstraint(
                check=~(Q(is_closed=True) & Q(approval_status="CANCELLED")),
                name="ox_close_appr_chk",
            ),
            # 退供：supplier 必填、customer 为空；非退供：customer 必填、supplier 为空
            models.CheckConstraint(
                name="ox_rtv_partner_chk",
                check=(
                    (Q(outbound_type="SUPPLIER_RETURN") & Q(supplier__isnull=False) & Q(customer__isnull=True)) |
                    (~Q(outbound_type="SUPPLIER_RETURN") & Q(customer__isnull=False) & Q(supplier__isnull=True))
                ),
            ),
        ]

    def __str__(self) -> str:
        return self.order_no or f"OUT@{self.id}"

    def save(self, *args, **kwargs):
        if not self.warehouse_id:
            raise ValidationError({"warehouse": "必须明确指定出库订单仓库"})
        if not self.pk and not self.order_no:
            self.order_no = DocSequence.next_code(
                doc_type="CK",
                warehouse=self.warehouse,
                owner=self.owner,
                biz_date=self.biz_date,
            )
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        errs = {}
        if not self.warehouse_id:
            errs["warehouse"] = "必须明确指定出库订单仓库"
        if self.is_closed and not self.close_reason:
            errs["close_reason"] = "订单关闭时必须提供关闭理由"
        # 可选：预计发货时间不得早于业务日期
        # if self.etd and self.biz_date and self.etd.date() < self.biz_date:
        #     errs["etd"] = "预计发货时间不能早于单据日期"
        if errs:
            from django.core.exceptions import ValidationError
            raise ValidationError(errs)

    STATUS_OWNER_APPROVED = "OWNER_APPROVED"

    @transaction.atomic
    def owner_approve(self, by_user=None, allow_backorder=True):
        """
        货主管理员确认：
        - 如果当前货主需要按订单金额收费，则先自动确认价格
        - 再置状态为 OWNER_APPROVED
        - 最后冻结库存
        """
        # # 1. 按 billing 规则决定是否需要价格
        # self.auto_confirm_pricing_if_required(by_user=by_user)

        # 1. 审核通过动作中：先冻结订单价格，再更新审核状态
        self.auto_confirm_pricing(by_user=by_user)

        # 2. 订单审核状态
        self.approval_status = self.STATUS_OWNER_APPROVED

        update_fields = ["approval_status", "updated_at"]
        if hasattr(self, "approved_by_ownermanager"):
            self.approved_by_ownermanager = by_user
            update_fields.append("approved_by_ownermanager")
        if hasattr(self, "approved_at_ownermanager"):
            self.approved_at_ownermanager = timezone.now()
            update_fields.append("approved_at_ownermanager")

        self.save(update_fields=update_fields)

        # 3. 冻结库存
        ob_services.allocate_inventory(self, by_user=by_user, allow_backorder=allow_backorder)

    def _calculate_final_order_amount(self):
        """
        用订单行 base_qty * base_price 计算最终订单金额，
        同时将每行金额写入 final_line_amount。
        当前约定：
        - OutboundOrderLine.base_price = 最终成交基本单位单价
        - 订单确认时自动冻结价格
        """
        lines = list(self.lines.filter(is_deleted=False))
        if not lines:
            raise ValidationError("订单没有可确认的明细行。")

        total = Decimal("0.00")
        for line in lines:
            qty = Decimal(line.base_qty or 0)
            price = Decimal(line.base_price or 0)

            # 如果你的业务允许 0 元商品，把这里改成 < 0
            if price <= 0:
                raise ValidationError(f"订单行 {line.line_no} 的价格未填写或不合法。")

            line_amount = (qty * price).quantize(Decimal("0.01"))
            line.final_line_amount = line_amount
            line.save(update_fields=["final_line_amount", "updated_at"])
            total += line_amount

        return total.quantize(Decimal("0.01"))

    def _freeze_line_amounts_and_total(self):
        """
        冻结订单行金额，并汇总最终订单金额。
        - final_line_amount = base_qty * base_price
        - final_order_amount = sum(final_line_amount)
        """
        lines = self.lines.filter(is_deleted=False)
        if not lines.exists():
            raise ValidationError("订单没有可确认的明细行。")

        total = Decimal("0.00")
        now_ts = timezone.now()

        for line in lines:
            qty = Decimal(line.base_qty or 0)
            price = Decimal(line.base_price or 0)

            if price <= 0:
                raise ValidationError(f"订单行 {line.line_no} 的价格未填写或不合法。")

            line_total = (qty * price).quantize(Decimal("0.01"))

            if line.final_line_amount != line_total:
                type(line).objects.filter(pk=line.pk).update(
                    final_line_amount=line_total,
                    updated_at=now_ts,
                )

            total += line_total

        return total.quantize(Decimal("0.01"))

    def auto_confirm_pricing(self, by_user=None):
        """
        在订单确认时自动确认价格。
        """
        # total = self._calculate_final_order_amount()
        total = self._freeze_line_amounts_and_total()
        self.final_order_amount = total
        self.pricing_status = PricingStatus.CONFIRMED
        self.priced_at = timezone.now()
        self.priced_by = by_user
        self.save(update_fields=[
            "final_order_amount",
            "pricing_status",
            "priced_at",
            "priced_by",
            "updated_at",
        ])

    def requires_order_amount_billing(self):
        """
        判断当前订单所属货主/仓库/日期，是否存在有效的
        DISPATCH + PERCENT_OF_ORDER_AMOUNT 规则。
        """
        from django.db.models import Q
        from allapp.billing.models import BillingRule
        from allapp.billing.enums import ChargeType, CalcMethod

        biz_date = self.biz_date
        return BillingRule.objects.filter(
            active=True,
            charge_type=ChargeType.DISPATCH,
            calc_method=CalcMethod.PERCENT_OF_ORDER_AMOUNT,
        ).filter(
            Q(owner_id=self.owner_id) | Q(owner__isnull=True)
        ).filter(
            Q(warehouse_id=self.warehouse_id) | Q(warehouse__isnull=True)
        ).filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=biz_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=biz_date),
        ).exists()

    def auto_confirm_pricing_if_required(self, by_user=None):
        """
        只有在当前订单需要按订单金额收费时，才自动确认价格。
        """
        if not self.requires_order_amount_billing():
            return

        total = self._calculate_final_order_amount()
        self.final_order_amount = total
        self.pricing_status = PricingStatus.CONFIRMED
        self.priced_at = timezone.now()
        self.priced_by = by_user
        self.save(update_fields=[
            "final_order_amount",
            "pricing_status",
            "priced_at",
            "priced_by",
            "updated_at",
        ])

class OutboundOrderLine(BaseModel):
    PACK_REQ_CHOICES = [
        ("NONE", "无（不需要打包）"),
        ("BAG", "袋装/气泡袋"),
        ("BOX", "装箱"),
        ("SHRINK", "缠绕/热缩"),
        ("PALLET", "打托/缠膜"),
    ]

    order = models.ForeignKey("OutboundOrder", verbose_name="订单",
                              on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey("products.Product", verbose_name="商品", on_delete=models.PROTECT)

    base_qty = models.DecimalField("基本数量", max_digits=14, decimal_places=3, default=0)
    base_price = models.DecimalField("基本价格", max_digits=14, decimal_places=4, default=0)
    base_uom = models.ForeignKey("products.ProductUom", verbose_name="基本单位",on_delete=models.PROTECT, null=True, blank=True)

    aux_qty = models.DecimalField("包装数量", max_digits=14, decimal_places=3, blank=True, null=True)
    aux_uom = models.ForeignKey("products.ProductPackage", verbose_name="包装单位", on_delete=models.PROTECT, blank=True, null=True)
    aux_price = models.DecimalField("包装价格", max_digits=14, decimal_places=4, blank=True, null=True)
    ratio = models.DecimalField("换算率(快照)", max_digits=14, decimal_places=4, blank=True, null=True)  # ←如保留，当快照用

    line_no = models.PositiveIntegerField("行号")

    lot_no = models.CharField("批号", max_length=50, blank=True, null=True)
    min_remaining_days = models.PositiveIntegerField("最短剩余效期(天)", blank=True, null=True)
    pack_requirement = models.CharField("打包要求", max_length=20, choices=PACK_REQ_CHOICES, default="NONE")
    pack_note = models.CharField("打包备注", max_length=120, blank=True, default="")
    note = models.CharField("明细备注", max_length=200, blank=True, null=True)

    final_line_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="最终行金额",
    )

    class Meta:
        verbose_name = "出库订单行"
        verbose_name_plural = "出库订单行"
        ordering = ["order_id", "line_no"]
        constraints = [
            models.UniqueConstraint(fields=["order", "line_no"], name="ux_out_line_no"),
            models.CheckConstraint(check=(Q(base_qty__gt=0) | Q(aux_qty__gt=0)), name="chk_out_qty_pos"),
            models.CheckConstraint(check=Q(base_qty__gte=0) & (Q(aux_qty__gte=0) | Q(aux_qty__isnull=True)),
                                   name="chk_out_qty_nonneg"),
            models.CheckConstraint(check=Q(base_price__gte=0) & (Q(aux_price__gte=0) | Q(aux_price__isnull=True)),
                                   name="chk_out_price_nonneg"),
        ]
        indexes = [
            models.Index(fields=["order", "product"], name="idx_out_order_prod"),
            models.Index(fields=["product", "lot_no"], name="idx_out_prod_lot"),
            models.Index(fields=["order", "lot_no"],   name="idx_out_order_lot"),
        ]

    def __str__(self):
        return f"{self.order.order_no}-{self.line_no}"

    def _pkg_ratio(self) -> Decimal:
        if not self.aux_uom_id:
            return Decimal("0")
        r = getattr(self.aux_uom, "qty_in_base", None)
        if not r:
            raise ValidationError({"aux_uom": "包装缺少换算系数"})
        return Decimal(r)

    def clean(self):
        errors = {}

        if isinstance(self.lot_no, str):
            self.lot_no = self.lot_no.strip() or None

        aux = self.aux_qty or Decimal("0")
        base = self.base_qty or Decimal("0")
        if aux <= 0 and base <= 0:
            errors["base_qty"] = "至少有一个数量必须大于 0"
        if aux and not self.aux_uom_id:
            errors["aux_uom"] = "填写了包装数量，必须指定包装单位。"
        if (self.aux_price is not None) and not self.aux_uom_id:
            errors["aux_price"] = "填写了包装价格，必须指定包装单位。"
        if self.base_price is not None and self.base_price < 0:
            errors["base_price"] = "基本价格不能为负。"
        if self.aux_price is not None and self.aux_price < 0:
            errors["aux_price"] = "包装价格不能为负。"

        if getattr(self.order, "owner_id", None) and getattr(self.product, "owner_id", None):
            if self.order.owner_id != self.product.owner_id:
                errors["product"] = "商品货主与出库单货主不一致"

        if self.product_id and self.base_uom_id and self.base_uom_id != self.product.base_uom_id:
            errors["base_uom"] = "基本单位必须等于商品的基本单位"

        if self.aux_uom_id and self.aux_uom.product_id != self.product_id:
            errors["aux_uom"] = "包装必须属于所选商品"

        if self.aux_uom_id and self.aux_qty and self.base_qty:
            ratio = self._pkg_ratio()
            expect_base = (self.aux_qty * ratio).quantize(Decimal("0.001"))
            if (expect_base - self.base_qty).copy_abs() > Decimal("0.001"):
                errors["base_qty"] = f"基本数量与包装数量不一致，应为 {expect_base}（当前 {self.base_qty}）。"

        # 可选：商品未启用批次时不允许填批号
        # if self.lot_no and hasattr(self.product, "batch_control") and not self.product.batch_control:
        #     errors["lot_no"] = "该商品未启用批次管理，批号必须留空"

        if errors:
            raise ValidationError(errors)

    def _ensure_line_no(self):
        if not self._state.adding or self.line_no:
            return
        Order = type(self.order)
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order_id)
            if not order.next_line_no:
                last = type(self).objects.filter(order_id=order.id).aggregate(m=Max("line_no"))["m"] or 0
                order.next_line_no = (last // 10 + 1) * 10 or 10
            self.line_no = order.next_line_no
            order.next_line_no += 10
            order.save(update_fields=["next_line_no"])

    def save(self, *args, **kwargs):
        # 自动带出基本单位
        if self.product_id and not self.base_uom_id:
            self.base_uom_id = self.product.base_uom_id

        # 包装→基本数量换算
        if (not self.base_qty or self.base_qty == 0) and self.aux_uom_id and self.aux_qty:
            ratio = self._pkg_ratio()
            self.base_qty = (Decimal(self.aux_qty) * ratio).quantize(Decimal("0.001"))

        # 仅给了包装价时，回填基本单价
        if (not self.base_price or self.base_price == 0) and self.aux_price and self.aux_uom_id:
            ratio = self._pkg_ratio()
            if ratio > 0:
                self.base_price = (Decimal(self.aux_price) / ratio).quantize(Decimal("0.0001"))

        # 若保留 ratio 字段，把换算率快照下来
        if self.aux_uom_id:
            try:
                self.ratio = self._pkg_ratio()
            except ValidationError:
                pass

        self.clean()
        self._ensure_line_no()
        return super().save(*args, **kwargs)

#===订单级扩展
class OutboundOrderExtra(BaseModel):
    order = models.OneToOneField("outbound.OutboundOrder", on_delete=models.PROTECT,
                                 related_name="extra", verbose_name="对应出库单")
    inbound_order = models.ForeignKey("inbound.InboundOrder", on_delete=models.PROTECT,
                                      null=True, blank=True, related_name="generated_outbound_rtv",
                                      verbose_name="来源入库单")

    vendor_rma_no = models.CharField("供应商RMA/授权号", max_length=40, blank=True, default="")
    reason_code   = models.CharField("退回原因代码", max_length=20, blank=True, default="")
    reason_desc   = models.CharField("退回原因描述", max_length=200, blank=True, default="")

    chargeback_flag        = models.BooleanField("是否扣款/索赔", default=False)
    expected_credit_amount = models.DecimalField("预计供应商贷项", max_digits=14, decimal_places=2,
                                                 null=True, blank=True, validators=[MinValueValidator(0)])
    currency = models.CharField("币种", max_length=3, blank=True, default="")

    carrier     = models.ForeignKey("baseinfo.CarrierCompany", on_delete=models.PROTECT,
                                    null=True, blank=True, verbose_name="承运商")
    vehicle_no  = models.CharField("车牌号", max_length=32, blank=True, default="")
    tracking_no = models.CharField("运单号/追踪号", max_length=64, blank=True, default="")
    seal_no     = models.CharField("封签号", max_length=40, blank=True, default="")

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name="rtv_approved")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "出库单头扩展"
        verbose_name_plural = "出库单头扩展"
        constraints = [
            models.CheckConstraint(
                name="chk_oextra_approved_pair",
                check=(Q(approved_by__isnull=True, approved_at__isnull=True) |
                       Q(approved_by__isnull=False, approved_at__isnull=False)),
            ),
            models.CheckConstraint(
                name="chk_oextra_credit_nonneg",
                check=Q(expected_credit_amount__gte=0) | Q(expected_credit_amount__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["vendor_rma_no"], name="ix_oextra_rma"),
            models.Index(fields=["tracking_no"], name="ix_oextra_track"),
        ]

    def clean(self):
        super().clean()
        if not self.order_id:
            raise ValidationError({"order": "必须先选择对应出库单"})
        if self.order.outbound_type != "SUPPLIER_RETURN":
            raise ValidationError("仅 SUPPLIER_RETURN 类型的出库单允许创建 OutboundOrderExtra。")
        # 规范化
        for f in ("vendor_rma_no", "tracking_no", "seal_no", "reason_code", "currency"):
            v = (getattr(self, f) or "").strip()
            if f in ("vendor_rma_no", "tracking_no", "seal_no", "currency"):
                v = v.upper()
            setattr(self, f, v)

    def __str__(self):
        return f"OOEX@{getattr(self.order, 'order_no', self.pk)}"

#===订单侧通用来源映射
class OrderLineSourceLink(models.Model):
    """
    通用：出库订单行 ← 多来源（入库回执/检验/其他单据）
    适用于 RTV/换货/借退等
    """
    order_line = models.ForeignKey(
        "outbound.OutboundOrderLine",
        on_delete=models.PROTECT,
        related_name="source_links",
        verbose_name="出库行",
    )

    # 通用来源（必须给一个）
    src_ct = models.ForeignKey(ContentType, on_delete=models.PROTECT, verbose_name="来源类型")
    src_id = models.BigIntegerField("来源主键", db_index=True)
    # 便捷访问（不落表）
    src_obj = GenericForeignKey(ct_field="src_ct", fk_field="src_id")

    # 数量（基本单位）
    plan_qty_base   = models.DecimalField("计划数量(基本)", max_digits=14, decimal_places=3,
                                          null=True, blank=True, validators=[MinValueValidator(0)])
    posted_qty_base = models.DecimalField("已过账数量(基本)", max_digits=14, decimal_places=3,
                                          default=0, validators=[MinValueValidator(0)])

    # 快照/追溯
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, verbose_name="商品")
    lot_no  = models.CharField("批号", max_length=60, blank=True, null=True)
    note    = models.CharField("备注", max_length=200, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "订单行来源映射"
        verbose_name_plural = "订单行来源映射"
        constraints = [
            # 同一订单行+同一来源唯一
            models.UniqueConstraint(fields=["order_line", "src_ct", "src_id"], name="ux_olsrc_unique"),
            # 数量非负 & 已过账 ≤ 计划（当计划存在时）
            models.CheckConstraint(
                name="ck_olsrc_qty_nonneg",
                check=Q(posted_qty_base__gte=0) & (Q(plan_qty_base__isnull=True) | Q(plan_qty_base__gte=0)),
            ),
            models.CheckConstraint(
                name="ck_olsrc_post_le_plan",
                check=Q(plan_qty_base__isnull=True) | Q(posted_qty_base__lte=F("plan_qty_base")),
            ),
        ]
        indexes = [
            models.Index(fields=["order_line"], name="ix_olsrc_line"),
            models.Index(fields=["src_ct", "src_id"], name="ix_olsrc_src"),
            models.Index(fields=["product", "lot_no"], name="ix_olsrc_prod_lot"),
        ]

    def clean(self):
        super().clean()
        # 商品一致性：与出库行商品必须一致
        if self.product_id and getattr(self.order_line, "product_id", None):
            if self.product_id != self.order_line.product_id:
                raise ValidationError({"product": "与出库行商品不一致"})
        # 批号规范化
        if isinstance(self.lot_no, str):
            self.lot_no = self.lot_no.strip().upper() or None

    def __str__(self):
        return f"SRC@L{self.order_line_id}:{self.src_ct_id}/{self.src_id}"


# --- 子菜单锚点（Proxy，无需迁移/建表） ---
from allapp.tasking.models import WmsTask  # 继承用，无表变更

class FuncWaveGenerate(WmsTask):
    class Meta:
        proxy = True
        verbose_name = "波次生成"
        verbose_name_plural = verbose_name

class FuncLabelBatch(WmsTask):
    class Meta:
        proxy = True
        verbose_name = "面单批量打印"
        verbose_name_plural = verbose_name

class FuncShippingBoard(WmsTask):
    class Meta:
        proxy = True
        verbose_name = "发运看板"
        verbose_name_plural = verbose_name
