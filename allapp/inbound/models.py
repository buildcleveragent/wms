import logging
from django.utils import timezone
from decimal import ROUND_HALF_UP
import datetime
from decimal import Decimal
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, F, Max
from django.db import models, transaction
from django.urls import reverse

from allapp.core.models import BaseModel,DocSequence
from allapp.products.models import Product,ProductPackage,ProductUom
from allapp.baseinfo.models import Owner,Supplier,Vehicle,CarrierCompany
from allapp.locations.models import Location,Warehouse

QTY_Q = Decimal("0.0001")  # 数量保留 3 位

def _q_qty_q(x):
    """将任意数值量化到 3 位（数量口径）"""
    return (Decimal(x) if isinstance(x, Decimal) else Decimal(str(x))).quantize(QTY_Q, rounding=ROUND_HALF_UP)

# 设置日志
logger = logging.getLogger(__name__)

def _infer_base_uom_code(owner_id, product_code):
    # 输入验证
    if owner_id is None or not product_code:
        return None

    try:
        # 使用 values() 获取所需字段，避免加载整个对象
        product = Product.objects.filter(owner_id=owner_id, code=product_code) \
                                 .values("base_uom__code") \
                                 .first()

        # 如果没有找到产品或没有基本单位，返回 None
        if not product or not product.get("base_uom__code"):
            logger.warning(f"Product with owner_id={owner_id} and code={product_code} does not have a base_uom.")
            return None

        return product["base_uom__code"]

    except Exception as e:
        # 捕获可能发生的异常并记录错误
        logger.error(f"Error fetching base_uom code for owner_id={owner_id}, product_code={product_code}: {str(e)}")
        return None

# ===================== 入库订单 =====================
class InboundOrder(BaseModel):
    SUBMIT_CHOICES = [("SUBMITTED", "已提交"), ("DRAFT", "未提交")]
    APPROVAL_CHOICES = [
        ("NOT_READY", "未到审核时机"),
        ("OWNER_PENDING", "待货主管理员审核"),
        ("OWNER_APPROVED", "货主管理员已审核通过,待仓库确认"),
        ("OWNER_REJECTED", "货主管理员已驳回"),
        ("WHS_PENDING", "待仓库管理员确认"),
        ("WHS_APPROVED", "仓库管理员已确认,待收货"),
        ("WHS_REJECTED", "仓库管理员已驳回"),
        ("CANCELLED", "已取消"),
    ]
    DELIVERY_METHOD_CHOICES = [("CIF", "到岸"), ("DISPATCH", "派车")]
    INBOUND_TYPE_CHOICES = [
        ("PURCHASE", "采购入库"),
        ("CUST_RETURN", "退货入库"),
        ("TRANSFER", "调拨入库"),
        ("OTHER_IN", "其他入库"),
    ]

    INBOUNDORDER_CREATOR_CHOICES=[
        ("OWNER_PURCHASER", "货主采购员"),
        ("OWNER_MANAGER", "货主管理员"),
        ("WH_MANAGER", "仓库管理员"),
    ]
    created_at = models.DateTimeField("制表时间", auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="制表人", on_delete=models.PROTECT,
                                   blank=True, null=True, related_name="%(class)s_created")

    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主", on_delete=models.PROTECT,related_name="inbound_orders")
    supplier = models.ForeignKey("baseinfo.Supplier", verbose_name="供应商", on_delete=models.PROTECT, related_name="inbound_orders")
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="仓库",on_delete=models.PROTECT, related_name="inbound_orders",editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    order_no = models.CharField("订单编号", max_length=100, unique=True)
    biz_date = models.DateField("日期", default=datetime.date.today)
    src_bill_no = models.CharField("源单号", max_length=100, blank=True, null=True)
    inbound_type = models.CharField("入库类型", max_length=15, choices=INBOUND_TYPE_CHOICES, default="PURCHASE")
    delivery_method = models.CharField("交货方式", max_length=15, choices=DELIVERY_METHOD_CHOICES, blank=True, null=True)
    eta = models.DateTimeField("预计到货时间", blank=True, null=True)

    submit_status = models.CharField("提交状态", max_length=15, choices=SUBMIT_CHOICES, default="DRAFT", db_index=True)
    approval_status = models.CharField("审核状态", max_length=15, choices=APPROVAL_CHOICES, default="NOT_READY", db_index=True)
    address = models.CharField("地址", max_length=100, blank=True, null=True)
    memo = models.CharField("备注", max_length=100, blank=True, default="")
    is_closed = models.BooleanField("订单关闭", default=False)
    close_reason = models.CharField("关闭理由", max_length=50, blank=True, null=True)
    # 审核人字段
    approved_by_ownermanager = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="货主管理员审核人", on_delete=models.PROTECT,  blank=True, null=True, related_name="appr_by_owner_in_ord" )
    approved_at_ownermanager = models.DateTimeField("货主管理员审核时间", blank=True, null=True)

    approved_by_warehouse = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="仓库管理员审核人", on_delete=models.PROTECT, blank=True, null=True,  related_name="appr_by_wh_inbound_orders" )
    approved_at_warehouse = models.DateTimeField("仓库管理员审核时间", blank=True, null=True )

    next_line_no = models.PositiveIntegerField("下一个订单行号", default=10)

    class Meta:
        verbose_name = "入库订单"
        verbose_name_plural = "入库订单"
        ordering = ["-biz_date", "-id"]
        permissions = [
            ("approve_as_owner_manager", "货主管理员可执行审核动作"),
            ("approve_as_wh_manager",   "仓库管理员可执行确认动作"),
            ("submit_as_owner_buyers", "货主业务员可提交入库订单"),
        ]
        indexes = [
            models.Index(fields=["owner", "biz_date", "submit_status"], name="ii_order_owner_date_status"),
            models.Index(fields=["approval_status"], name="ii_order_approval_status"),
        ]
        constraints = [
            models.CheckConstraint(
                name="chk_inb_submit_status",
                check=Q(submit_status__in=["DRAFT", "SUBMITTED"]),
            ),
            models.CheckConstraint(
                check=models.Q(approval_status__in=["NOT_READY","OWNER_PENDING", "OWNER_APPROVED", "OWNER_REJECTED", "WHS_PENDING",
                                                    "WHS_APPROVED", "WHS_REJECTED", "CANCELLED"]),
                name="valid_approval_status"
            ),
            models.CheckConstraint(
                #已关闭订单，不能取消
                check = ~(Q(is_closed=True) & Q(approval_status__in=["CANCELLED"])),
                name="close_approval_status_check"
            ),
        ]

    def __str__(self):
        return self.order_no or f"INB@{self.id}"

    def get_absolute_url(self):
        return reverse("inbound:order_detail", kwargs={"pk": self.pk})

    def save(self, *args, **kwargs):
        # 仅在创建时赋号
        if not self.pk and not self.order_no:
            self.order_no = DocSequence.next_code(
                doc_type="INB",
                warehouse=self.warehouse,
                owner=self.owner,
                biz_date=self.biz_date,
                # 可选覆盖（默认用 DocSequence.DEFAULT_*）
                # width=DocSequence.DEFAULT_WIDTH,
                # fmt="{prefix}-{yyyy}{mm}{dd}-{wh}-{own}-{seq}",
            )
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        # 校验订单关闭逻辑
        if self.is_closed and not self.close_reason:
            errors["close_reason"] = "订单关闭时必须提供关闭理由"

        if self.pk:
            old = type(self).objects.only("approval_status").get(pk=self.pk)
            if old.approval_status != self.approval_status:
                # 只有系统动作才允许改；动作会设置 _allow_status_write=True
                if not getattr(self, "_allow_status_write", False):
                    errors["approval_status"] = "状态由系统维护，禁止手工修改。"

        if errors:
            raise ValidationError(errors)

        # ——【动作接口，系统唯一入口】——

    @transaction.atomic
    def owner_approve(self, user):
        if self.submit_status != "SUBMITTED" or self.approval_status != "OWNER_PENDING":
            raise ValidationError("仅在【已提交&待货主管理员审核】时可通过。")
        self._allow_status_write = True
        self.approval_status = "OWNER_APPROVED"  # 或根据你的流转规则改为 "WHS_PENDING"
        self.approved_by_ownermanager = user
        self.approved_at_ownermanager = timezone.now()
        self.save(update_fields=["approval_status", "approved_by_ownermanager", "approved_at_ownermanager"])

    @transaction.atomic
    def owner_reject(self, user):
        if self.submit_status != "SUBMITTED" or self.approval_status != "OWNER_PENDING":
            raise ValidationError("仅在【已提交&待货主管理员审核】时可驳回。")
        self._allow_status_write = True
        self.approval_status = "OWNER_REJECTED"
        self.approved_by_ownermanager = user
        self.approved_at_ownermanager = timezone.now()
        self.save(update_fields=["approval_status", "approved_by_ownermanager", "approved_at_ownermanager"])


    def _check_wh_confirmable(self):
        """仓库确认的前置校验；不通过时一次性给出所有原因。"""
        reasons = []
        if self.is_closed:
            reasons.append("单据已关闭。")
        if self.submit_status != "SUBMITTED":
            reasons.append(f"提交状态为 {self.submit_status}，必须为 SUBMITTED。")
        # 如果你采用“单路径”（推荐）：仓库仅接受 WHS_PENDING
        # need = ["WHS_PENDING",]
        need = ["WHS_PENDING", "OWNER_APPROVED"]
        if self.approval_status not in need:
            reasons.append(f"审核状态为 {self.approval_status}，必须为 { ' 或 '.join(need) }。")

        # 这里还可以加更多业务前置，如：是否存在订单行、数量>0、必填字段等
        if not self.lines.exists():
            reasons.append("没有任何订单行。")
        # 示例：若要求每行必须指定批次
        # if self.lines.filter(lot_no__isnull=True).exists():
        #     reasons.append("存在未填写批次的订单行。")

        if reasons:
            # 一次性抛出所有原因
            raise ValidationError("；".join(reasons))

    @transaction.atomic
    def wh_confirm(self, user):

        # if self.submit_status != "SUBMITTED" or self.approval_status not in ["WHS_PENDING", "OWNER_APPROVED"]:
        #     raise ValidationError("仅在【待仓库确认/货主已通过】时可确认。")
        self._check_wh_confirmable()
        self._allow_status_write = True
        self.approval_status = "WHS_APPROVED"
        self.approved_by_warehouse = user
        self.approved_at_warehouse = timezone.now()
        self.save(update_fields=["approval_status", "approved_by_warehouse", "approved_at_warehouse"])

        # —— 自动创建【收货任务草稿】（幂等） —— #
        # 采用就地导入，避免潜在循环依赖
        from . import services as inbound_services
        inbound_services.create_receive_task_draft(self, by_user=user)

    @transaction.atomic
    def wh_reject(self, user):
        if self.submit_status != "SUBMITTED" or self.approval_status not in ["WHS_PENDING", "OWNER_APPROVED"]:
            raise ValidationError("仅在【待仓库确认/货主已通过】时可驳回。")
        self._allow_status_write = True
        self.approval_status = "WHS_REJECTED"
        self.approved_by_warehouse = user
        self.approved_at_warehouse = timezone.now()
        self.save(update_fields=["approval_status", "approved_by_warehouse", "approved_at_warehouse"])

    # —— 提交动作（系统入口；允许从草稿或被货主驳回后重新提交）——
    @transaction.atomic
    def submit_by_owner_buyers(self, user):
        if self.is_closed:
            raise ValidationError("已关闭的订单不能提交。")
        if self.submit_status == "SUBMITTED":
            raise ValidationError("订单已提交。")

        # 允许从 DRAFT 或 OWNER_REJECTED 进入提交；其它状态禁止
        # if self.approval_status not in ["OWNER_PENDING", "OWNER_REJECTED"]:
        #     raise ValidationError("当前审核状态不允许提交。")

        # 系统动作允许写状态（配合 clean() 的防手改逻辑）
        self._allow_status_write = True

        # 进入“已提交”，并把审核状态归位到 OWNER_PENDING
        self.submit_status = "SUBMITTED"
        self.approval_status = "OWNER_PENDING"
        self.save(update_fields=["submit_status", "approval_status"])

class InboundOrderLine(BaseModel):
    order = models.ForeignKey(
        "InboundOrder", verbose_name="订单",
        on_delete=models.PROTECT, related_name="lines"
    )
    product = models.ForeignKey("products.Product", verbose_name="商品",on_delete=models.PROTECT)

    # 单位 价格 数量（基础计量必存；包装可选）
    base_uom = models.CharField("基本单位", max_length=30, blank=True, null=True)
    base_price = models.DecimalField("基本价格", max_digits=14, decimal_places=4, default=0)
    base_qty = models.DecimalField("基本数量", max_digits=18, decimal_places=3, default=0)

    aux_uom = models.ForeignKey("products.ProductPackage", verbose_name="包装单位", on_delete=models.PROTECT, blank=True, null=True)
    aux_price = models.DecimalField("包装价格", max_digits=14, decimal_places=4, blank=True, null=True)
    aux_qty = models.DecimalField("包装数量", max_digits=18, decimal_places=3, blank=True, null=True)
    ratio = models.DecimalField("换算率", max_digits=14, decimal_places=4, blank=True, null=True)

    # # 统一口径的小计（以基本单位计价；可选但强烈建议持久化，做报表更快）
    # line_amount = models.DecimalField("行小计(基本单位口径)", max_digits=16, decimal_places=4, default=0)

    # 行号：自动分配（按订单步长10）
    line_no = models.PositiveIntegerField("行号")

    # 批次/效期（订单可留空，通常在收货明细上实录）
    lot_no = models.CharField("批号", max_length=50, blank=True, null=True)
    min_remaining_days = models.PositiveIntegerField("最短剩余效期(天)", blank=True, null=True)
    expiry_not_earlier_than = models.DateField("到期不得早于", blank=True, null=True)
    pack_requirement = models.CharField("打包要求", max_length=200, blank=True, null=True)
    note = models.CharField("明细备注", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "入库订单行"
        verbose_name_plural = "入库订单行"
        ordering = ["order_id", "line_no"]
        constraints = [
            models.CheckConstraint(
                name="chk_inb_ratio_pos_when_aux",
                check=Q(aux_uom__isnull=True) | Q(ratio__gt=0),
            ),
            # 同单据内行号唯一
            models.UniqueConstraint(fields=["order", "line_no"], name="ux_inb_order_line_no"),
            # 至少一个数量 > 0
            models.CheckConstraint(
                check=(Q(base_qty__gt=0) | Q(aux_qty__gt=0)),
                name="chk_inb_qty_one_positive",
            ),
            # 两个数量都不得为负
            models.CheckConstraint(
                check=Q(base_qty__gte=0) & (Q(aux_qty__gte=0) | Q(aux_qty__isnull=True)),
                name="chk_inb_qty_non_negative",
            ),
            # 价格非负
            models.CheckConstraint(
                check=Q(base_price__gte=0) & (Q(aux_price__gte=0) | Q(aux_price__isnull=True)),
                name="chk_inb_price_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["order", "product"], name="idx_inb_order_product"),
            models.Index(fields=["product", "lot_no"], name="idx_inb_product_lot"),
            models.Index(fields=["order", "lot_no"],   name="idx_inb_order_lot"),
        ]

    def __str__(self):
        return f"{self.order_id}-{self.line_no}"
    # return f"{self.order.order_no}-{self.line_no}"
    #__str__用了self.order.order_no 会触发一次关系访问；量大时可考虑
    #f"{self.order_id}-{self.line_no}"  以避开额外查询（非刚需）。

    # --- 内部：取包装换算比（1 个该包装=？个基本单位）
    def _pkg_ratio(self) -> Decimal:
        if not self.aux_uom_id:
            return Decimal("0")
        # 假设 ProductPackage 有 base_qty（若你的字段叫 qty_in_base/factor，改这里）
        ratio = getattr(self.aux_uom, "qty_in_base", None)
        if not ratio:
            raise ValidationError({"aux_uom": "包装缺少换算系数"})
        return Decimal(ratio)

    def clean(self):
        errors = {}

        # 去空格
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

        # 货主一致
        if getattr(self.order, "owner_id", None) and getattr(self.product, "owner_id", None):
            if self.order.owner_id != self.product.owner_id:
                errors["product"] = "商品货主与入库单货主不一致"

        # base_uom 必须等于 product.base_uom
        # if self.product_id and self.base_uom_id and self.base_uom_id != self.product.base_uom_id:
        #     errors["base_uom"] = "基本单位必须等于商品的基本单位"
        if self.product_id and self.base_uom and self.base_uom != self.product.base_uom.code:
            errors["base_uom"] = "基本单位必须等于商品的基本单位"

        # 包装单位必须属于该商品
        if self.aux_uom_id and self.aux_uom.product_id != self.product_id:
            errors["aux_uom"] = "包装单位必须属于所选商品"

        # 批/效期控制一致
        p = self.product
        # if p:
        #     if not p.batch_control and self.lot_no:
        #         errors["lot_no"] = "该商品未启用批次管理，批次号必须留空"

        # 若同时给了 base_qty 与 aux_qty，校验换算一致（容差 1e-3）
        if self.aux_uom_id and self.aux_qty and self.base_qty:
            ratio = self._pkg_ratio()
            expect_base = (self.aux_qty* ratio).quantize(Decimal("0.001"))
            if (expect_base - self.base_qty).copy_abs() > Decimal("0.001"):
                errors["base_qty"] = f"基本数量与包装数量不一致，应为 {expect_base}（当前 {self.base_qty}）。"

        if errors:
            raise ValidationError(errors)

    def _ensure_line_no(self):
        """为新增行分配行号（按订单步长10；并发安全）"""
        if not self._state.adding or self.line_no:
            return
        # 如果订单表有 next_line_no 字段，优先用它
        Order = type(self.order)
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order_id)
            if not order.next_line_no:
                    last = type(self).objects.filter(order_id=order.id).aggregate(m=Max("line_no"))["m"] or 0
                    order.next_line_no = (last // 10 + 1) * 10 or 10

            self.line_no = order.next_line_no
            order.next_line_no += 10
            order.save(update_fields=["next_line_no"])

    #bulk_create不会走save()，不会分配line_no。若将来需要批量导入，请在服务层循环分配或改用分批create()。
    def save(self, *args, **kwargs):
        # 自动带出基础单位
        # if self.product_id and not self.base_uom_id:
        #     self.base_uom = self.product.base_uom_id
        if self.product_id and not self.base_uom:
            self.base_uom = self.product.base_uom.code  # 或 .name，视你的约定

        # 规范化数量：如未给 base_qty 且给了 aux_qty，则按包装换算自动带出
        if (not self.base_qty or self.base_qty == 0) and self.aux_uom_id and self.aux_qty:
            ratio = self._pkg_ratio()
            self.base_qty = (Decimal(self.aux_qty) * ratio).quantize(Decimal("0.001"))

        # 统一单价与小计：以基本单位口径计算
        # unit_price_base = Decimal(self.base_price or 0)
        # if unit_price_base == 0 and self.aux_price and self.aux_uom_id:
        #     ratio = self._pkg_ratio()
        #     if ratio > 0:
        #         unit_price_base = (Decimal(self.aux_price) / ratio).quantize(Decimal("0.0001"))
        # self.line_amount = (Decimal(self.base_qty or 0) * unit_price_base).quantize(Decimal("0.0001"))
        if (not self.base_price or self.base_price == 0) and self.aux_price and self.aux_uom_id:
            ratio = self._pkg_ratio()
            if ratio > 0:
                self.base_price = (Decimal(self.aux_price) / ratio).quantize(Decimal("0.0001"))

        self.ratio = self._pkg_ratio()

        # 分配行号
        self._ensure_line_no()
        # 兜底校验
        self.clean()
        super().save(*args, **kwargs)

# ===================== 入库单（收货凭证） =====================
class InboundReceipt(BaseModel):
    SUBMIT_CHOICES = [("SUBMITTED", "提交"), ("DRAFT", "未提交")]
    INBOUND_TYPE_CHOICES = [
        ("PURCHASE", "采购入库"),
        ("CUST_RETURN", "退货入库"),
        ("TRANSFER", "调拨入库"),
        ("OTHER_IN", "其他入库"),
    ]
    DELIVERY_METHOD_CHOICES = [("CIF", "到岸"), ("DISPATCH", "派车")]

    receipt_no = models.CharField("单据编号", max_length=100, unique=True)  # 去掉 db_index
    order = models.ForeignKey("inbound.InboundOrder", verbose_name="关联订单",on_delete=models.PROTECT, related_name="receipts", blank=True, null=True)
    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主", on_delete=models.PROTECT, related_name="inbound_receipts")
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="所属仓库",on_delete=models.PROTECT, related_name="inbound_receipts",editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    supplier = models.ForeignKey("baseinfo.Supplier", verbose_name="供应商",on_delete=models.PROTECT, related_name="inbound_receipts")
    # campus_name = models.CharField("园区(快照)", max_length=80, blank=True, null=True)  # 若无实体就留快照
    biz_date = models.DateField("日期")  # 默认由服务层设置为今天
    # 推荐直接用 FK；若暂时不能，请把 CharField 方案与 clean() 校验补齐
    order_no_snap = models.CharField("关联订单编号(快照)", max_length=100, blank=True, null=True)

    inbound_type = models.CharField("入库类型", max_length=16, choices=INBOUND_TYPE_CHOICES, default="PURCHASE")
    delivery_method = models.CharField("交货方式", max_length=16, choices=DELIVERY_METHOD_CHOICES, blank=True, null=True)
    submit_status = models.CharField("提交状态", max_length=16, choices=SUBMIT_CHOICES, default="DRAFT", db_index=True)

    address = models.CharField("地址", max_length=200, blank=True, null=True)
    stevedore_team = models.CharField("装卸队", max_length=80, blank=True, null=True)

    made_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="制单人",
                                on_delete=models.PROTECT, blank=True, null=True,
                                related_name="made_inbound_receipts")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="审核人",
                                    on_delete=models.PROTECT, blank=True, null=True,
                                    related_name="approved_inbound_receipts")
    approved_at = models.DateTimeField("审核时间", blank=True, null=True)

    class Meta:
        verbose_name = "入库收货单"
        verbose_name_plural = "入库收货单"
        ordering = ["-biz_date", "-id"]
        constraints = [
            # 审批成对
            models.CheckConstraint(
                name="chk_rcpt_approved_pair",
                check=(Q(approved_by__isnull=True, approved_at__isnull=True) |
                       Q(approved_by__isnull=False, approved_at__isnull=False)),
            ),
            # 审批必须在提交之后
            models.CheckConstraint(
                name="chk_rcpt_approved_after_submit",
                check=Q(approved_by__isnull=True) | Q(submit_status="SUBMITTED"),
            ),
            # 已提交必须有制单人（按你的流程决定是否需要）
            models.CheckConstraint(
                name="chk_rcpt_submit_need_maker",
                check=~Q(submit_status="SUBMITTED", made_by__isnull=True),
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "submit_status", "biz_date"], name="ix_rcpt_own_stat_date"),
            models.Index(fields=["supplier", "biz_date"], name="ix_rcpt_sup_date"),
        ]

    def __str__(self):
        return self.receipt_no

    def clean(self):
        errors = {}
        # 订单一致性（如果有 FK）
        if self.order_id:
            if self.order.owner_id != self.owner_id:
                errors["owner"] = "收货单货主与关联订单货主不一致。"
            if self.order.supplier_id != self.supplier_id:
                errors["supplier"] = "收货单供应商与关联订单供应商不一致。"
            if self.order.warehouse_id != self.warehouse_id:
                errors["warehouse"] = "收货单仓库与关联订单仓库不一致。"

        # 审批与提交关系（与 DB 约束一致，给出友好文案）
        if self.approved_by_id and self.submit_status != "SUBMITTED":
            errors["submit_status"] = "仅已提交的单据才能审核。"
        if self.submit_status == "SUBMITTED" and not self.made_by_id:
            errors["made_by"] = "提交单据前必须有制单人。"

        if errors:
            from django.core.exceptions import ValidationError
            raise ValidationError(errors)

class InboundReceiptLine(BaseModel):
    """
    入库单行（回执凭证行）
    - 数量统一以“基本单位”落账：base_qty
    - 包装信息以“快照”形式记录：pack_code/pack_size/pack_qty/pack_barcode（可选）
    - SKU 条码也仅保存“快照”：sku_barcode
    """

    receipt = models.ForeignKey(
        "InboundReceipt",
        verbose_name="入库单",
        on_delete=models.PROTECT,
        related_name="lines",
    )

    # 由服务层分配，不设 default
    line_no = models.PositiveIntegerField("行号")

    # —— 商品快照（允许未绑 FK） —— #
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        null=True, blank=True,
        verbose_name="商品",
    )

    product_code = models.CharField("商品编号", max_length=60)
    product_name = models.CharField("名称", max_length=120)
    spec = models.CharField("规格型号", max_length=120, blank=True, null=True)
    # SKU 条码快照（仅用于呈现/追溯；真正解析在记录层做过了）
    sku_barcode = models.CharField("SKU条码(快照)", max_length=128, blank=True, null=True)

    # —— 库位：回执阶段可空 —— #
    location = models.ForeignKey(
        "locations.Location",
        verbose_name="仓位",
        on_delete=models.PROTECT,
        related_name="inbound_receipt_lines",
        null=True, blank=True,
    )

    # —— 数量/计价（统一口径：基本单位） —— #
    order_qty = models.DecimalField("订购数量", max_digits=14, decimal_places=3, blank=True, null=True)
    base_qty = models.DecimalField("本次入库数(基本单位)", max_digits=14, decimal_places=3, default=0)
    base_uom = models.CharField("基本单位(快照)", max_length=20)  # 必填快照
    unit_price = models.DecimalField("单价(每基本单位)", max_digits=14, decimal_places=4, default=0)

    # —— 包装快照（可选） —— #
    pack_code = models.CharField("包装编码(快照)", max_length=20, blank=True, null=True)
    pack_size = models.DecimalField("换算(基本单位/包,快照)", max_digits=18, decimal_places=4, blank=True, null=True)
    pack_qty = models.DecimalField("本次包数(快照)", max_digits=14, decimal_places=3, blank=True, null=True)
    pack_barcode = models.CharField("包装条码(快照)", max_length=128, blank=True, null=True)

    # —— 批/效期快照 —— #
    lot_no = models.CharField("批号", max_length=60, blank=True, null=True)
    mfg_date = models.DateField("生产日期", blank=True, null=True)
    exp_date = models.DateField("有效期至", blank=True, null=True)

    # qty_desc = models.CharField("数量描述", max_length=120, blank=True, null=True)
    # inv_desc = models.CharField("库存描述", max_length=120, blank=True, null=True)
    note = models.CharField("备注", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "入库单明细"
        verbose_name_plural = "入库单明细"
        ordering = ["receipt_id", "line_no"]
        constraints = [
            # 同单内行号唯一 & 行号>=1
            models.UniqueConstraint(fields=["receipt", "line_no"], name="uq_rcpt_line_no"),
            models.CheckConstraint(check=Q(line_no__gte=1), name="ck_rcpt_lineno_pos"),
            # 数量/单价非负
            models.CheckConstraint(
                check=Q(base_qty__gte=0),
                name="ck_rcpt_qty_nonneg",
            ),
            models.CheckConstraint(
                check=Q(unit_price__gte=0),
                name="ck_rcpt_price_nonneg",
            ),
            # 批/效期：exp >= mfg（任一为空则不校验）
            models.CheckConstraint(
                check=Q(exp_date__isnull=True) | Q(mfg_date__isnull=True) | Q(exp_date__gte=F("mfg_date")),
                name="ck_rcpt_exp_ge_mfg",
            ),
            # 包装一致性：有包数就必须有正的换算
            models.CheckConstraint(
                check=Q(pack_qty__isnull=True) | (Q(pack_size__isnull=False) & Q(pack_size__gt=0)),
                name="ck_rcpt_pack_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["receipt", "product_code"], name="ix_rcpt_prodcode"),
            models.Index(fields=["product", "lot_no"], name="ix_rcpt_prod_lot"),
            models.Index(fields=["receipt", "lot_no", "exp_date"], name="ix_rcpt_lot_exp"),
        ]

    def __str__(self):
        return f"{self.receipt.receipt_no}-{self.line_no}"

    # —— 内部：按 owner+code 拿 Product（避免硬导入） —— #
    def _get_product(self):
        if self.product_id:
            return self.product
        owner_id = getattr(self.receipt, "owner_id", None)
        if owner_id is None or not self.product_code:
            return None
        Product = apps.get_model("products", "Product")
        # 你基线里 (owner, code) 应该唯一
        #.select_related("base_uom") 的作用是：在查询 Product 时，把它的外键 base_uom 一起用 SQL JOIN 提前取回（eager loading）。这样后面访问 p.base_uom、p.base_uom.code 时不会再额外发 SQL，可避免 N+1 查询。
        return Product.objects.filter(owner_id=owner_id, code=self.product_code).select_related("base_uom").first()

    # —— 业务校验 —— #
    def clean(self):
        errors = {}

        # 非负（DB 兜底，这里给出友好报错）
        if self.base_qty is not None and self.base_qty < 0:
            errors["base_qty"] = "基本数量不能为负"
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "单价不能为负"
        if self.mfg_date and self.exp_date and self.exp_date < self.mfg_date:
            errors["exp_date"] = "有效期不得早于生产日期"

        # 包装一致性
        if self.pack_qty is not None:
            if not self.pack_size or self.pack_size <= 0:
                errors["pack_size"] = "有包数时需提供>0的包装换算(基本单位/包)"

        # 商品一致性
        p = self._get_product()
        if p:
            # 货主一致
            if getattr(self.receipt, "owner_id", None) and getattr(p, "owner_id", None):
                if self.receipt.owner_id != p.owner_id:
                    errors["product"] = "商品货主与入库单货主不一致"
            # code 一致
            if self.product_code and self.product_code != p.code:
                errors["product_code"] = "与所选商品不一致"
            # 批/效期控制（若你的 Product 有对应布尔开关）
            # if getattr(p, "batch_control", False) is False and self.lot_no:
            #     errors["lot_no"] = "该商品未启用批次管理，批号必须留空"
            # if getattr(p, "expiry_control", False) is False and (self.mfg_date or self.exp_date):
            #     errors["exp_date"] = "该商品未启用效期管理，生产/到期日必须留空"

        # 审核后不允许改动关键快照
        if getattr(self.receipt, "approved_at", None) and self.pk:
            old = type(self).objects.filter(pk=self.pk).values(
               "pack_barcode", "product_code", "product_name", "spec", "sku_barcode", "base_uom", "pack_code", "pack_size"
            ).first()
            if old:
                dirty = [f for f, oldv in old.items() if getattr(self, f) != oldv]
                if dirty:
                    errors["__all__"] = f"单据已审核，禁止修改：{', '.join(dirty)}"

        if isinstance(self.product_code, str):
            self.product_code = self.product_code.strip() or ""

        if errors:
            raise ValidationError(errors)


    # —— 保存：补齐快照 & 基本数量换算 —— #
    def save(self, *args, **kwargs):
        p = self._get_product()

        # 1) 商品快照
        if p:
            self.product_code = self.product_code or p.code
            self.product_name = self.product_name or p.name
            self.spec = self.spec or getattr(p, "spec", None)
            self.sku_barcode = self.sku_barcode or getattr(p, "barcode", None)  # 若有默认SKU条码
            if not self.base_uom and getattr(p, "base_uom_id", None):
                self.base_uom = p.base_uom.code

        # 2) 包装→基本单位换算（有包数就补 base_qty；若 base_qty 已给则不覆盖，只校验一致）
        # if self.pack_qty is not None and self.pack_size:
        #     calc_base = (self.pack_qty or Decimal("0")) * self.pack_size
        #     if self.base_qty is None:
        #         self.base_qty = calc_base
        #     else:
        #         # 可接受轻微小数误差（如需）
        #         if abs(Decimal(self.base_qty) - calc_base) > Decimal("0.0005"):
        #             raise ValidationError({"base_qty": "基本数量与包装换算不一致"})
        # ……（前置自动带出商品、base_uom 等逻辑）……

        # ========= 包装 → 基本数量（按口径：给了 pack 就推导 base_qty；若 base_qty 已填写则校验一致）=========
        has_pack = self.pack_qty is not None and self.pack_size is not None and self.pack_size > 0
        if has_pack:
            # 计算按包装折算的基本数量（量化到 3 位）
            calc_base = _q_qty_q((self.pack_qty or Decimal("0")) * self.pack_size)

            # 注意：base_qty 字段 null=False 且 default=0，所以“未填写”通常表现为 0
            base_val = _q_qty_q(self.base_qty or Decimal("0"))

            if base_val == 0:
                # 视为“未填写”，自动补齐
                self.base_qty = calc_base
            else:
                # 已填写：校验一致（两边都量化后再比较，避免精度误差）
                if (base_val - calc_base).copy_abs() > Decimal("0.0005"):
                    raise ValidationError({"base_qty": f"基本数量与包装换算不一致，应为 {calc_base}（当前 {base_val}）。"})

        # 3) 兜底：确保有基本单位快照
        if not self.base_uom:
            raise ValidationError({"base_uom": "基本单位不能为空（未选商品时需手填）"})

        # 4) 业务校验
        self.clean()
        return super().save(*args, **kwargs)

# ===================== 销售退货 =====================
class InboundOrderReturnInfo(BaseModel):
    """退货专属补充（与 InboundOrder 1:1）"""
    # 仅允许绑定“销售退货入库”单据
    order = models.OneToOneField(
        "InboundOrder",
        on_delete=models.PROTECT,
        related_name="return_info",
        verbose_name="入库订单"
    )

    # 可选：枚举/字典
    SOURCE_CHANNELS = [
        ("ONLINE", "线上"),
        ("STORE", "门店"),
        ("THIRD_PARTY", "三方/3PL"),
        ("OTHER", "其他"),
    ]
    REFUND_STATUSES = [
        ("PENDING", "待处理"),
        ("APPROVED", "已通过"),
        ("PAID", "已退款"),
        ("REJECTED", "已驳回"),
    ]

    rma_no = models.CharField("退货授权号", max_length=40, blank=True, null=True)
    source_channel = models.CharField("来源渠道", max_length=20, choices=SOURCE_CHANNELS, blank=True, null=True)
    orig_outbound_order_no = models.CharField("原出库单号", max_length=40, blank=True, null=True)

    reason_code = models.CharField("退货原因", max_length=20, blank=True, null=True)  # 或 FK 到“原因字典”
    photos_required = models.BooleanField("需要照片", default=False)

    refund_amount = models.DecimalField("退款金额", max_digits=14, decimal_places=2, blank=True, null=True)
    refund_status = models.CharField("退款状态", max_length=20, choices=REFUND_STATUSES, blank=True, null=True)

    class Meta:
        verbose_name = "销售退货入库"
        verbose_name_plural = "销售退货入库"
        constraints = [
            # 退款非负（为空不校验）
            models.CheckConstraint(
                name="ck_ret_refund_nonneg",  # 注意<=30字符
                check=Q(refund_amount__isnull=True) | Q(refund_amount__gte=0),
            ),
        ]
        indexes = [
            models.Index(fields=["rma_no"], name="ix_ret_rma_no"),
            models.Index(fields=["orig_outbound_order_no"], name="ix_ret_orig_out_no"),
            models.Index(fields=["refund_status"], name="ix_ret_refund_status"),
            models.Index(fields=["source_channel"], name="ix_ret_src_channel"),
        ]

    def clean(self):
        errors = {}

        # 1) 仅允许绑定“销售退货入库”单据（按你 InboundOrder 的枚举值修正）
        try:
            inbound_type = getattr(self.order, "inbound_type", None)
        except Exception:
            inbound_type = None
        if inbound_type and inbound_type != "CUST_RETURN":
            errors["order"] = "仅可在‘销售退货入库’订单上创建退货信息。"

        # 2) 退款非负的人性化报错（DB 兜底已加）
        if self.refund_amount is not None and self.refund_amount < 0:
            errors["refund_amount"] = "退款金额不能为负。"

        # 3) RMA 在同 Owner 下不重复（如果需要此口径）
        # 只能在代码里查，因为本表没有 owner 冗余列：
        if self.rma_no and getattr(self, "order_id", None):
            owner_id = getattr(self.order, "owner_id", None)
            if owner_id:
                qs = type(self).objects.filter(order__owner_id=owner_id, rma_no=self.rma_no)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                if qs.exists():
                    errors["rma_no"] = "同一货主下该 RMA 编号已存在。"

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"ReturnInfo#{self.order_id or 'NA'}"

class ReturnInspection(BaseModel):
    # 关联
    order_line = models.ForeignKey(
        "InboundOrderLine", on_delete=models.PROTECT,
        related_name="inspections", verbose_name="来源订单行"
    )
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, related_name="return_inspections")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, related_name="return_inspections",editable=False,default=settings.DEFAULT_WAREHOUSE_ID)

    # 实物标识
    lot_no = models.CharField("批号", max_length=60, blank=True, null=True)
    exp_date = models.DateField("有效期至", blank=True, null=True)
    serial_no = models.CharField("序列号", max_length=60, blank=True, null=True)
    serial_no_norm = models.CharField("序列号(归一)", max_length=60, blank=True, null=True)

    # 检验/处置
    CONDITION_CHOICES = [("A","全新"),("B","完好"),("C","轻微问题"),("D","严重问题")]
    condition = models.CharField("品相", max_length=10, choices=CONDITION_CHOICES, blank=True, null=True)

    DISPOSITION_CHOICES = [
        ("RESTOCK","再上架"),
        ("REFURBISH","翻新"),
        ("REPAIR","返修"),
        ("SCRAP","报废"),
        ("RTV","退供应商"),
    ]
    disposition = models.CharField("处置", max_length=20, choices=DISPOSITION_CHOICES)

    qty = models.DecimalField("数量", max_digits=14, decimal_places=3)

    STATUS_CHOICES = [("OPEN","待审核"), ("APPROVED","已审核"), ("POSTED","已下发执行")]
    status = models.CharField("状态", max_length=10, choices=STATUS_CHOICES, default="OPEN", db_index=True)

    inspected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name="检验人")
    inspected_at = models.DateTimeField("检验时间", null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_return_inspections", verbose_name="审核人")
    approved_at = models.DateTimeField("审核时间", null=True, blank=True)

    note = models.CharField("备注", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "退货检验处置"
        verbose_name_plural = "退货检验处置"
        ordering = ["-created_at", "-id"]
        constraints = [
            # 数量必须为正
            models.CheckConstraint(check=Q(qty__gt=0), name="chk_retinsp_qty_pos"),
            # 若有序列号，数量必须为 1（空串视为无序列号，在 save() 里归一到 None）
            models.CheckConstraint(
                check=Q(serial_no__isnull=True) | Q(qty=1),
                name="chk_retinsp_serial_qty_eq1",
            ),
            # 审核成对
            models.CheckConstraint(
                name="chk_retinsp_approved_pair",
                check=((Q(approved_by__isnull=True) & Q(approved_at__isnull=True)) |
                       (Q(approved_by__isnull=False) & Q(approved_at__isnull=False))),
            ),
            # 序列号唯一（按 owner，使用归一列实现大小写无关；None 可重复）
            models.UniqueConstraint(
                fields=["owner", "serial_no_norm"],
                name="ux_retinsp_owner_serial_norm",
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse", "status", "disposition"], name="idx_retinsp_wh_status_disp"),
            models.Index(fields=["order_line"], name="idx_retinsp_orderline"),
            models.Index(fields=["owner", "lot_no"], name="idx_retinsp_owner_lot"),

        ]

    def __str__(self):
        return f"RI-{self.order_line_id}-{self.disposition}"

    def clean(self):
        errs = {}

        # 获取商品（行上通常有 product外键；若无就略过）
        prod = getattr(self.order_line, "product", None)

        # 序列化商品规则
        if prod and getattr(prod, "serial_control", False):
            if not self.serial_no:
                errs["serial_no"] = "序列化商品必须录入序列号"
            if self.qty != 1:
                errs["qty"] = "序列化商品每条检验记录数量必须为 1"
        else:
            # 非序列化商品不应录入序列号（避免误录）
            if self.serial_no:
                errs["serial_no"] = "非序列化商品不应录入序列号"

        # 审核约束（示例）：已审核需有 inspected_by/at
        if self.status in {"APPROVED", "POSTED"} and (not self.inspected_by_id or not self.inspected_at):
            errs["inspected_by"] = "审核前必须先完成检验（检验人/时间必填）"

        if errs:
            raise ValidationError(errs)

    def save(self, *args, **kwargs):
        # 强制同步 owner/warehouse 快照（防绕过；若想更松，可只在缺失时同步）
        if self.order_line_id:
            o = self.order_line.order
            self.owner_id = getattr(o, "owner_id", self.owner_id)
            self.warehouse_id = getattr(o, "warehouse_id", self.warehouse_id)

        # 归一化序列号：空白 => None；非空 => 去空格并转大写
        s = (self.serial_no or "").strip()
        self.serial_no = s or None
        self.serial_no_norm = (s.upper() if s else None)

        self.clean()
        return super().save(*args, **kwargs)

# ===================== 批次 =====================
class Lot(BaseModel):
    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主",
                              on_delete=models.PROTECT, related_name="lots")

    # 商品：允许为空，但要求提供 product_code（快照）
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT,
                                null=True, blank=True, related_name="lots")
    product_code = models.CharField("商品编号(快照)", max_length=60)

    lot_no = models.CharField("批号", max_length=60)
    lot_no_norm = models.CharField("批号(规范化)", max_length=64, db_index=True)

    supplier = models.ForeignKey("baseinfo.Supplier", verbose_name="供应商",
                                 on_delete=models.PROTECT, blank=True, null=True, related_name="lots")
    mfg_date = models.DateField("生产日期", blank=True, null=True)
    exp_date = models.DateField("有效期至", blank=True, null=True)

    default_qa_status = models.CharField("默认质检状态", max_length=20, blank=True, null=True)

    extra = models.JSONField("扩展属性", default=dict, blank=True)  # 避免 None

    class Meta:
        verbose_name = "批次"
        verbose_name_plural = "批次"
        ordering = ["product_code", "lot_no"]
        constraints = [
            # 同 owner + product_code(大写口径) + lot_no_norm 唯一
            models.UniqueConstraint(
                fields=["owner", "product_code", "lot_no_norm"],
                name="ux_lot_owner_prod_lotnorm",
            ),
            # exp >= mfg
            models.CheckConstraint(
                name="chk_lot_exp_ge_mfg",
                check=(Q(exp_date__isnull=True) | Q(mfg_date__isnull=True) | Q(exp_date__gte=F("mfg_date"))),
            ),
            # （可选）有 product 时再加一道唯一（PG 支持条件唯一）
            # models.UniqueConstraint(
            #     fields=["owner", "product", "lot_no_norm"],
            #     name="ux_lot_owner_prodFK_lotnorm",
            #     condition=Q(product__isnull=False),
            # ),
        ]
        indexes = [
            # ⚠️ 删除与唯一重复的索引 idx_lot_owner_prod_lotnorm
            models.Index(fields=["owner", "product_code", "exp_date"], name="idx_lot_owner_prod_exp"),
            models.Index(fields=["owner", "supplier", "product_code"], name="idx_lot_owner_supp_prod"),
        ]

    def __str__(self):
        return f"{self.product_code}-{self.lot_no}"

    def clean(self):
        errs = {}

        # 1) 基础：批号必填
        if not self.lot_no:
            errs["lot_no"] = "批号不能为空"

        # 2) 商品与货主一致
        if self.product_id and self.owner_id:
            prod_owner_id = getattr(self.product, "owner_id", None)
            if prod_owner_id and prod_owner_id != self.owner_id:
                errs["product"] = "商品所属货主与批次的货主不一致"

        # 3) 若绑定了 product，则 product_code 必须等于 product.code
        if self.product_id and self.product_code:
            if self.product_code != getattr(self.product, "code", None):
                errs["product_code"] = "product_code 与所选商品不一致"

        # 4) 也可在这里检查 product_code 是否为空（如果你希望强制快照）
        if not self.product_code:
            errs["product_code"] = "商品编号(快照)不能为空"

        if errs:
            raise ValidationError(errs)

    def save(self, *args, **kwargs):
        # 规范化：批号（去所有空白 + 大写）；原始 lot_no 保持仅 strip，保留用户原样以便打印
        if isinstance(self.lot_no, str):
            self.lot_no = self.lot_no.strip()
            self.lot_no_norm = "".join(self.lot_no.split()).upper() if self.lot_no else ""

        # 规范化：product_code（统一口径，避免唯一约束被大小写/空白击穿）
        if isinstance(self.product_code, str):
            self.product_code = self.product_code.strip().upper()

        # 若绑定 product 且未填 product_code，则快照一次（也可选择报错以强制一致性）
        if self.product_id and not self.product_code:
            self.product_code = self.product.code.strip().upper()

        # 业务校验
        self.clean()
        return super().save(*args, **kwargs)

class LotWarehouse(BaseModel):
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, related_name="lot_warehouses")
    lot = models.ForeignKey("Lot", on_delete=models.PROTECT, related_name="warehouses")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, related_name="lot_warehouses",editable=False,default=settings.DEFAULT_WAREHOUSE_ID)

    # 建议给出 choices，若暂不收口也可保留自由文本
    QA_STATUS = [
        ("RELEASED", "可用"),
        ("HOLD", "冻结/待检"),
        ("QUARANTINE", "隔离"),
        ("RETURN", "待退供应商"),
        ("SCRAP_PENDING", "待报废"),
    ]
    qa_status = models.CharField("质检状态", max_length=20, blank=True, null=True, choices=QA_STATUS)

    blocked_until = models.DateTimeField("解封时间", blank=True, null=True)
    blocked_reason = models.CharField("冻结原因", max_length=120, blank=True, null=True)

    class Meta:
        verbose_name = "批次-仓库状态"
        verbose_name_plural = "批次-仓库状态"
        ordering = ["warehouse_id", "lot_id"]

        constraints = [
            # 唯一约束：同批次在同仓库仅一条（owner 冗余，可去掉；若保留 owner，也可以继续用原三列唯一）
            models.UniqueConstraint(fields=["lot", "warehouse"], name="ux_lotwh_unique"),
            # 冻结字段成对：有 blocked_until 必须有原因
            models.CheckConstraint(
                name="ck_lotwh_block_pair",
                check=Q(blocked_until__isnull=True) | (Q(blocked_until__isnull=False) & Q(blocked_reason__isnull=False)),
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse", "qa_status"], name="idx_lotwh_wh_qastat"),
            models.Index(fields=["warehouse", "blocked_until"], name="idx_lotwh_wh_blockuntil"),
            models.Index(fields=["owner", "warehouse", "qa_status"], name="idx_lotwh_owner_wh_qastat"),
        ]

    def __str__(self):
        return f"LotWH[{self.warehouse_id}]-{self.lot_id}"

    # —— 业务校验 —— #
    def clean(self):
        errs = {}

        # 1) owner 与 lot.owner 必须一致
        if self.lot_id and self.owner_id:
            lot_owner_id = getattr(self.lot, "owner_id", None)
            if lot_owner_id and lot_owner_id != self.owner_id:
                errs["owner"] = "货主与批次的货主不一致"

        # 2) blocked_until 不得早于“现在”（业务口径：冻结结束时间应是未来；若允许历史记录，可移除此条）
        if self.blocked_until and self.blocked_until <= timezone.now():
            errs["blocked_until"] = "解封时间应晚于当前时间"

        # 3) qa_status 口径（若不使用 choices，可在此自定义白名单）
        # if self.qa_status and self.qa_status not in {"RELEASED","HOLD","QUARANTINE","RETURN","SCRAP_PENDING"}:
        #     errs["qa_status"] = "质检状态不在允许的字典内"

        if errs:
            raise ValidationError(errs)

    # —— 保存：必要的默认/同步 —— #
    def save(self, *args, **kwargs):
        # 若未给 qa_status，尝试沿用 Lot 的默认
        if not self.qa_status and self.lot_id:
            default_status = getattr(self.lot, "default_qa_status", None)
            if default_status:
                self.qa_status = default_status

        self.clean()
        return super().save(*args, **kwargs)

