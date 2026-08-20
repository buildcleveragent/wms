# salesapp/models.py
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from allapp.baseinfo.models import Customer, Owner
from allapp.core.models import BaseModel
from allapp.products.models import Product  # 你现有商品模型

# 如有 Warehouse / UOM 模型，也可在此引入

User = get_user_model()


def sale_mini_review_image_path(instance, filename):
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return f"sale-mini/reviews/{instance.review_id}/{uuid.uuid4().hex}{suffix}"


# —— 组织 / 集团架构（数据隔离）——
class BizOrg(BaseModel):
    class OrgType(models.TextChoices):
        GROUP = "group", "集团公司"
        FRANCHISE = "franchise", "加盟公司"
        PARTNER = "partner", "合作方"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="biz_orgs")  # 强隔离
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=64)
    org_type = models.CharField(max_length=20, choices=OrgType.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        unique_together = (("owner", "code"),)
        indexes = [models.Index(fields=["owner", "org_type"])]


# 业务员（可直接复用 User；这里加业务档案）
class Salesperson(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="salespersons")
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="sales_profile")
    org = models.ForeignKey(
        BizOrg,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="salespersons",
    )
    employee_no = models.CharField(max_length=64, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        unique_together = (("owner", "user"),)
        indexes = [models.Index(fields=["owner", "org"])]


# 渠道
class Channel(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="channels")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=64)

    class Meta:
        unique_together = (("owner", "code"),)


# 客户-渠道归属
class CustomerChannel(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="customer_channels")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="channels")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="customers")

    class Meta:
        unique_together = (("owner", "customer", "channel"),)


# —— 小程序商城：买家、地址、上架配置 ——
class MiniProgramUser(BaseModel):
    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="mini_program_users",
    )
    user = models.ForeignKey(
        User,
        verbose_name="关联账号",
        on_delete=models.PROTECT,
        related_name="mini_program_profiles",
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="mini_program_users",
    )
    openid = models.CharField("微信 OpenID", max_length=128, blank=True, default="")
    unionid = models.CharField("微信 UnionID", max_length=128, blank=True, default="")
    nickname = models.CharField("昵称", max_length=80, blank=True, default="")
    avatar_url = models.CharField("头像地址", max_length=500, blank=True, default="")
    phone = models.CharField("手机号", max_length=32, blank=True, default="")

    class Meta:
        verbose_name = "小程序用户"
        verbose_name_plural = "小程序用户"
        indexes = [
            models.Index(fields=["owner", "customer"]),
            models.Index(fields=["openid"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["owner", "user"], name="ux_mini_owner_user"),
        ]

    def __str__(self):
        return self.nickname or getattr(self.user, "username", "") or f"mini-user-{self.pk}"


class MiniCustomerAddress(BaseModel):
    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="mini_customer_addresses",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="mini_addresses",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="addresses",
        null=True,
        blank=True,
    )
    contact = models.CharField("联系人", max_length=80)
    phone = models.CharField("联系电话", max_length=40)
    province = models.CharField("省份", max_length=30, blank=True, default="")
    city = models.CharField("城市", max_length=30, blank=True, default="")
    district = models.CharField("区县", max_length=30, blank=True, default="")
    detail = models.CharField("详细地址", max_length=200)
    is_default = models.BooleanField("默认地址", default=False)

    class Meta:
        verbose_name = "小程序收货地址"
        verbose_name_plural = "小程序收货地址"
        indexes = [
            models.Index(fields=["owner", "customer", "is_default"]),
            models.Index(fields=["buyer_user", "is_default"]),
        ]

    @property
    def full_address(self):
        return "".join([self.province, self.city, self.district, self.detail])

    def __str__(self):
        return f"{self.contact} {self.phone} {self.full_address}"


class SaleMiniBanner(BaseModel):
    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_banners",
    )
    title = models.CharField("标题", max_length=120)
    image_url = models.CharField("图片地址", max_length=500)
    link_type = models.CharField("链接类型", max_length=30, blank=True, default="")
    link_value = models.CharField("链接内容", max_length=120, blank=True, default="")
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        verbose_name = "商城轮播图"
        verbose_name_plural = "商城轮播图"
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["owner", "is_active", "sort_order"])]

    def __str__(self):
        return self.title


class SaleProductConfig(BaseModel):
    class StockDisplay(models.TextChoices):
        STATUS = "STATUS", "库存状态"
        EXACT = "EXACT", "准确库存"
        HIDDEN = "HIDDEN", "不展示库存"

    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name="sale_product_configs",
        verbose_name="货主",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sale_mini_configs",
        verbose_name="商品",
    )
    sale_price = models.DecimalField(
        "商城售价", max_digits=12, decimal_places=4, null=True, blank=True
    )
    market_price = models.DecimalField(
        "划线价", max_digits=12, decimal_places=4, null=True, blank=True
    )
    is_listed = models.BooleanField("已上架", default=False)
    is_recommended = models.BooleanField("推荐商品", default=False)
    is_hot = models.BooleanField("热销商品", default=False)
    is_new = models.BooleanField("新品", default=False)
    stock_display = models.CharField(
        "库存展示方式",
        max_length=12,
        choices=StockDisplay.choices,
        default=StockDisplay.STATUS,
    )
    enable_qty_rules = models.BooleanField("启用起购及递增限制", default=False)
    min_order_qty = models.DecimalField("起购数量", max_digits=12, decimal_places=3, default=1)
    max_order_qty = models.DecimalField(
        "最大购买量", max_digits=12, decimal_places=3, null=True, blank=True
    )
    multiple_qty = models.DecimalField("购买递增量", max_digits=12, decimal_places=3, default=1)
    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        verbose_name = "商城商品配置"
        verbose_name_plural = "商城商品配置"
        ordering = ["sort_order", "product__code"]
        indexes = [
            models.Index(fields=["owner", "is_listed", "is_active"]),
            models.Index(fields=["owner", "is_recommended", "sort_order"]),
            models.Index(fields=["owner", "is_hot", "sort_order"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["owner", "product"], name="ux_sale_cfg_owner_product"),
            models.CheckConstraint(
                condition=models.Q(min_order_qty__gt=0),
                name="ck_sale_cfg_min_qty_gt_0",
            ),
            models.CheckConstraint(
                condition=models.Q(multiple_qty__gt=0),
                name="ck_sale_cfg_multiple_gt_0",
            ),
        ]

    def __str__(self):
        return f"{self.owner_id}/{self.product_id}"

    def clean(self):
        super().clean()
        if not self.is_listed:
            return
        errors = {}
        if self.product_id and self.owner_id != self.product.owner_id:
            errors["owner"] = "上架配置货主必须等于商品货主"
        if self.product_id and not self.product.category_id:
            errors["product"] = "商品未分类，不能上架"
        elif self.product_id and not self.product.category.has_active_path():
            errors["product"] = "商品分类链存在停用分类，不能上架"
        if errors:
            raise ValidationError(errors)


class SaleMiniCart(BaseModel):
    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_carts",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_carts",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="carts",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "商城购物车"
        verbose_name_plural = "商城购物车"
        indexes = [
            models.Index(fields=["owner", "customer", "is_active"]),
            models.Index(fields=["buyer_user", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "customer", "buyer_user"],
                name="ux_sale_mini_cart_scope",
            ),
        ]

    def __str__(self):
        return f"cart:{self.owner_id}/{self.customer_id}/{self.buyer_user_id or '-'}"


class SaleMiniCartItem(BaseModel):
    cart = models.ForeignKey(
        SaleMiniCart,
        verbose_name="购物车",
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        verbose_name="商品",
        on_delete=models.PROTECT,
        related_name="sale_mini_cart_items",
    )
    order_uom = models.CharField("购买单位", max_length=32)
    qty = models.DecimalField("数量", max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "商城购物车商品"
        verbose_name_plural = "商城购物车商品"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["cart", "product"]),
            models.Index(fields=["product", "order_uom"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product", "order_uom"],
                name="ux_sale_mini_cart_item",
            ),
            models.CheckConstraint(
                condition=models.Q(qty__gt=0),
                name="ck_sale_mini_cart_qty_gt_0",
            ),
        ]

    def __str__(self):
        return f"{self.cart_id}:{self.product_id}/{self.order_uom} x {self.qty}"


class SaleMiniOrderMapping(BaseModel):
    class PaymentStatus(models.TextChoices):
        OFFLINE = "OFFLINE", "线下付款"
        UNPAID = "UNPAID", "待付款"
        PAID = "PAID", "已付款"
        REFUNDING = "REFUNDING", "退款中"
        REFUNDED = "REFUNDED", "已退款"
        CANCELLED = "CANCELLED", "已取消"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_order_mappings",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_order_mappings",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="order_mappings",
        null=True,
        blank=True,
    )
    outbound_order = models.OneToOneField(
        "outbound.OutboundOrder",
        verbose_name="出库订单",
        on_delete=models.PROTECT,
        related_name="sale_mini_mapping",
    )
    payment_status = models.CharField(
        "支付状态",
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.OFFLINE,
    )
    source = models.CharField("订单来源", max_length=30, default="sale-mini")
    goods_amount = models.DecimalField("商品金额", max_digits=18, decimal_places=2, default=0)
    adjustment_amount = models.DecimalField(
        "调整金额",
        max_digits=18,
        decimal_places=2,
        default=0,
        help_text="订单级调整金额，负数表示优惠，正数表示加价。",
    )
    payable_amount = models.DecimalField("应付金额", max_digits=18, decimal_places=2, default=0)
    pay_deadline_at = models.DateTimeField("支付截止时间", null=True, blank=True)
    paid_at = models.DateTimeField("支付时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城订单映射"
        verbose_name_plural = "商城订单映射"
        indexes = [
            models.Index(fields=["owner", "customer", "created_at"]),
            models.Index(fields=["payment_status"]),
            models.Index(fields=["payment_status", "pay_deadline_at"]),
            models.Index(fields=["owner", "payable_amount"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(payable_amount__gte=0),
                name="ck_sale_mini_mapping_payable_nonneg",
            ),
        ]

    def __str__(self):
        return f"{self.source}:{self.outbound_order_id}"


class SaleMiniCouponTemplate(BaseModel):
    class CouponType(models.TextChoices):
        AMOUNT = "AMOUNT", "金额券"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_coupon_templates",
    )
    code = models.CharField("模板编码", max_length=64)
    title = models.CharField("模板名称", max_length=120)
    coupon_type = models.CharField(
        "优惠券类型",
        max_length=20,
        choices=CouponType.choices,
        default=CouponType.AMOUNT,
    )
    threshold_amount = models.DecimalField("使用门槛", max_digits=18, decimal_places=2, default=0)
    discount_amount = models.DecimalField("优惠金额", max_digits=18, decimal_places=2)
    effective_from = models.DateField("生效日期")
    effective_to = models.DateField("失效日期", null=True, blank=True)
    total_limit = models.PositiveIntegerField("发放总量", default=0, help_text="0 表示不限量")
    per_customer_limit = models.PositiveIntegerField(
        "每客户限领数量", default=0, help_text="0 表示不限量"
    )
    is_stackable = models.BooleanField("允许叠加使用", default=True)

    class Meta:
        verbose_name = "商城优惠券模板"
        verbose_name_plural = "商城优惠券模板"
        indexes = [
            models.Index(fields=["owner", "code"]),
            models.Index(fields=["owner", "effective_from", "effective_to"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["owner", "code"], name="ux_sale_mini_coupon_tpl_code"),
            models.CheckConstraint(
                condition=models.Q(threshold_amount__gte=0),
                name="ck_sale_mini_coupon_tpl_threshold_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gt=0),
                name="ck_sale_mini_coupon_tpl_discount_gt_0",
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.title}"


class SaleMiniCoupon(BaseModel):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "可用"
        LOCKED = "LOCKED", "已锁定"
        USED = "USED", "已使用"
        RELEASED = "RELEASED", "已释放"
        EXPIRED = "EXPIRED", "已过期"
        VOID = "VOID", "已作废"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_coupons",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_coupons",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="coupons",
        null=True,
        blank=True,
    )
    template = models.ForeignKey(
        SaleMiniCouponTemplate,
        verbose_name="优惠券模板",
        on_delete=models.PROTECT,
        related_name="coupons",
    )
    coupon_no = models.CharField("优惠券编号", max_length=64, unique=True)
    status = models.CharField(
        "状态", max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    locked_mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="锁定订单",
        on_delete=models.PROTECT,
        related_name="locked_coupons",
        null=True,
        blank=True,
    )
    used_mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="使用订单",
        on_delete=models.PROTECT,
        related_name="used_coupons",
        null=True,
        blank=True,
    )
    locked_at = models.DateTimeField("锁定时间", null=True, blank=True)
    used_at = models.DateTimeField("使用时间", null=True, blank=True)
    expires_at = models.DateTimeField("过期时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城优惠券"
        verbose_name_plural = "商城优惠券"
        indexes = [
            models.Index(fields=["owner", "customer", "status"]),
            models.Index(fields=["buyer_user", "status"]),
            models.Index(fields=["locked_mapping"]),
            models.Index(fields=["used_mapping"]),
        ]

    def __str__(self):
        return self.coupon_no


class SaleMiniOrderAdjustment(BaseModel):
    class AdjustmentType(models.TextChoices):
        DISCOUNT_STEP = "DISCOUNT_STEP", "满减"
        COUPON = "COUPON", "优惠券"
        POINTS = "POINTS", "积分抵扣"
        MANUAL = "MANUAL", "人工调整"

    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", "预览"
        LOCKED = "LOCKED", "已锁定"
        CONFIRMED = "CONFIRMED", "已确认"
        RELEASED = "RELEASED", "已释放"
        REVERSED = "REVERSED", "已冲销"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_adjustments",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_adjustments",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="adjustments",
        null=True,
        blank=True,
    )
    mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="adjustments",
        null=True,
        blank=True,
    )
    adjustment_no = models.CharField("调整单号", max_length=64, unique=True)
    adjustment_type = models.CharField("调整类型", max_length=20, choices=AdjustmentType.choices)
    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.PREVIEW)
    title = models.CharField("调整说明", max_length=120)
    amount = models.DecimalField(
        "调整金额",
        max_digits=18,
        decimal_places=2,
        help_text="有符号调整金额，负数表示减少应付。",
    )
    source_model = models.CharField("来源模型", max_length=80, blank=True, default="")
    source_id = models.CharField("来源记录 ID", max_length=80, blank=True, default="")
    source_code = models.CharField("来源编号", max_length=120, blank=True, default="")
    locked_at = models.DateTimeField("锁定时间", null=True, blank=True)
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)
    released_at = models.DateTimeField("释放时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城订单调整"
        verbose_name_plural = "商城订单调整"
        indexes = [
            models.Index(fields=["owner", "customer", "adjustment_type"]),
            models.Index(fields=["mapping", "status"]),
            models.Index(fields=["source_model", "source_id"]),
        ]

    def __str__(self):
        return f"{self.adjustment_no} {self.amount}"


class SaleMiniPointLedger(BaseModel):
    class TxType(models.TextChoices):
        EARN = "EARN", "获得"
        FREEZE = "FREEZE", "冻结"
        CONSUME = "CONSUME", "消耗"
        RELEASE = "RELEASE", "释放"
        REFUND = "REFUND", "退回"
        ADJUST = "ADJUST", "调整"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_point_ledgers",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_point_ledgers",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="point_ledgers",
        null=True,
        blank=True,
    )
    mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="point_ledgers",
        null=True,
        blank=True,
    )
    tx_no = models.CharField("流水号", max_length=64, unique=True)
    tx_type = models.CharField("流水类型", max_length=20, choices=TxType.choices)
    points_delta = models.IntegerField("积分变动", default=0)
    frozen_delta = models.IntegerField("冻结积分变动", default=0)
    amount = models.DecimalField("关联金额", max_digits=18, decimal_places=2, default=0)
    note = models.CharField("说明", max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "商城积分流水"
        verbose_name_plural = "商城积分流水"
        indexes = [
            models.Index(fields=["owner", "customer", "created_at"]),
            models.Index(fields=["buyer_user", "created_at"]),
            models.Index(fields=["mapping", "tx_type"]),
        ]

    def __str__(self):
        return f"{self.tx_no} {self.tx_type}"


class SaleMiniDistributionRecord(BaseModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "待结算"
        SETTLED = "SETTLED", "已结算"
        REVERSED = "REVERSED", "已冲销"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_distribution_records",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_distribution_records",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="distribution_orders",
        null=True,
        blank=True,
    )
    referrer = models.ForeignKey(
        MiniProgramUser,
        verbose_name="推荐人",
        on_delete=models.PROTECT,
        related_name="distribution_referrals",
    )
    mapping = models.OneToOneField(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="distribution_record",
    )
    status = models.CharField("状态", max_length=20, choices=Status.choices, default=Status.PENDING)
    commission_rate = models.DecimalField("佣金比例", max_digits=7, decimal_places=4, default=0)
    base_amount = models.DecimalField("计佣金额", max_digits=18, decimal_places=2, default=0)
    commission_amount = models.DecimalField("佣金金额", max_digits=18, decimal_places=2, default=0)
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)
    settled_at = models.DateTimeField("结算时间", null=True, blank=True)
    reversed_at = models.DateTimeField("冲销时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城分销记录"
        verbose_name_plural = "商城分销记录"
        indexes = [
            models.Index(fields=["owner", "referrer", "status"]),
            models.Index(fields=["mapping", "status"]),
        ]

    def __str__(self):
        return f"{self.mapping_id}/{self.referrer_id}/{self.commission_amount}"


class SaleMiniPayment(BaseModel):
    class Channel(models.TextChoices):
        WECHAT_JSAPI = "WECHAT_JSAPI", "微信小程序支付"
        INTERNAL_ZERO = "INTERNAL_ZERO", "内部零元结算"

    class Status(models.TextChoices):
        CREATED = "CREATED", "已创建"
        PREPAY = "PREPAY", "已预下单"
        PAID = "PAID", "已支付"
        CLOSED = "CLOSED", "已关闭"
        REFUNDING = "REFUNDING", "退款中"
        REFUNDED = "REFUNDED", "已退款"
        FAILED = "FAILED", "失败"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_payments",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_payments",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="payments",
        null=True,
        blank=True,
    )
    mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    payment_no = models.CharField("支付流水号", max_length=64, unique=True)
    out_trade_no = models.CharField("商户支付单号", max_length=64, unique=True)
    channel = models.CharField(
        "支付渠道", max_length=20, choices=Channel.choices, default=Channel.WECHAT_JSAPI
    )
    status = models.CharField(
        "支付状态", max_length=20, choices=Status.choices, default=Status.CREATED
    )
    amount = models.DecimalField("支付金额", max_digits=18, decimal_places=2)
    amount_cents = models.PositiveIntegerField("支付金额（分）")
    currency = models.CharField("币种", max_length=8, default="CNY")
    prepay_id = models.CharField("微信预支付 ID", max_length=128, blank=True, default="")
    transaction_id = models.CharField(
        "微信支付单号",
        max_length=128,
        blank=True,
        null=True,
        unique=True,
        default=None,
    )
    trade_state = models.CharField("微信交易状态", max_length=40, blank=True, default="")
    trade_state_desc = models.CharField("微信交易状态说明", max_length=200, blank=True, default="")
    request_payload = models.JSONField("预支付请求报文", default=dict, blank=True)
    client_pay_params = models.JSONField("小程序支付参数", default=dict, blank=True)
    prepay_response = models.JSONField("预支付响应报文", default=dict, blank=True)
    callback_payload = models.JSONField("支付回调报文", default=dict, blank=True)
    expires_at = models.DateTimeField("支付过期时间", null=True, blank=True)
    paid_at = models.DateTimeField("支付时间", null=True, blank=True)
    closed_at = models.DateTimeField("关闭时间", null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField("重试次数", default=0)
    next_reconcile_at = models.DateTimeField("下次对账时间", null=True, blank=True)
    last_error = models.CharField("最后错误", max_length=300, blank=True, default="")
    requires_manual_action = models.BooleanField("需要人工处理", default=False)

    class Meta:
        verbose_name = "商城支付单"
        verbose_name_plural = "商城支付单"
        indexes = [
            models.Index(fields=["owner", "status", "created_at"]),
            models.Index(fields=["mapping", "status"]),
            models.Index(
                fields=["status", "next_reconcile_at"],
                name="salesapp_sa_status_2d5c52_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="ck_sale_mini_payment_amount_nonneg",
            ),
        ]

    def __str__(self):
        return self.payment_no


class SaleMiniRefund(BaseModel):
    class Source(models.TextChoices):
        USER_REQUEST = "USER_REQUEST", "用户申请"
        LATE_PAYMENT = "LATE_PAYMENT", "延迟到账自动退款"

    class Status(models.TextChoices):
        CREATED = "CREATED", "已创建"
        PROCESSING = "PROCESSING", "处理中"
        SUCCESS = "SUCCESS", "退款成功"
        ABNORMAL = "ABNORMAL", "退款异常"
        CLOSED = "CLOSED", "已关闭"
        FAILED = "FAILED", "失败"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_refunds",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_refunds",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="refunds",
        null=True,
        blank=True,
    )
    payment = models.ForeignKey(
        SaleMiniPayment,
        verbose_name="支付单",
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    source = models.CharField(
        "退款来源",
        max_length=24,
        choices=Source.choices,
        default=Source.USER_REQUEST,
    )
    idempotency_key = models.CharField(
        "幂等键",
        max_length=128,
        unique=True,
        null=True,
        blank=True,
    )
    refund_no = models.CharField("退款流水号", max_length=64, unique=True)
    out_refund_no = models.CharField("商户退款单号", max_length=64, unique=True)
    refund_id = models.CharField("微信退款单号", max_length=128, blank=True, default="")
    status = models.CharField(
        "退款状态", max_length=20, choices=Status.choices, default=Status.CREATED
    )
    amount = models.DecimalField("退款金额", max_digits=18, decimal_places=2)
    amount_cents = models.PositiveIntegerField("退款金额（分）")
    total_amount_cents = models.PositiveIntegerField("原订单金额（分）")
    currency = models.CharField("币种", max_length=8, default="CNY")
    reason = models.CharField("退款原因", max_length=120, blank=True, default="")
    request_payload = models.JSONField("退款请求报文", default=dict, blank=True)
    response_payload = models.JSONField("退款响应报文", default=dict, blank=True)
    callback_payload = models.JSONField("退款回调报文", default=dict, blank=True)
    requested_at = models.DateTimeField("申请退款时间", null=True, blank=True)
    success_at = models.DateTimeField("退款成功时间", null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField("重试次数", default=0)
    next_retry_at = models.DateTimeField("下次重试时间", null=True, blank=True)
    last_error = models.CharField("最后错误", max_length=300, blank=True, default="")
    requires_manual_action = models.BooleanField("需要人工处理", default=False)

    class Meta:
        verbose_name = "商城退款单"
        verbose_name_plural = "商城退款单"
        indexes = [
            models.Index(fields=["owner", "status", "created_at"]),
            models.Index(fields=["payment", "status"]),
            models.Index(fields=["refund_id"]),
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["requires_manual_action", "updated_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="ck_sale_mini_refund_amount_nonneg",
            ),
        ]

    def __str__(self):
        return self.refund_no


class SaleMiniAfterSaleRequest(BaseModel):
    class RequestType(models.TextChoices):
        REFUND = "REFUND", "仅退款"
        RETURN_REFUND = "RETURN_REFUND", "退货退款"
        EXCHANGE = "EXCHANGE", "换货"

    class Status(models.TextChoices):
        PENDING = "PENDING", "待处理"
        APPROVED = "APPROVED", "已同意"
        REJECTED = "REJECTED", "已拒绝"
        CANCELLED = "CANCELLED", "已取消"
        CLOSED = "CLOSED", "已关闭"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_after_sales",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_after_sales",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="after_sale_requests",
        null=True,
        blank=True,
    )
    mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="after_sale_requests",
    )
    request_no = models.CharField("售后申请单号", max_length=64, unique=True)
    request_type = models.CharField(
        "售后类型",
        max_length=20,
        choices=RequestType.choices,
        default=RequestType.REFUND,
    )
    status = models.CharField(
        "处理状态", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    amount = models.DecimalField("申请金额", max_digits=18, decimal_places=2, default=0)
    reason = models.CharField("申请原因", max_length=300, blank=True, default="")
    requested_at = models.DateTimeField("申请时间", null=True, blank=True)
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    review_note = models.CharField("审核意见", max_length=300, blank=True, default="")

    class Meta:
        verbose_name = "商城售后申请"
        verbose_name_plural = "商城售后申请"
        indexes = [
            models.Index(fields=["owner", "customer", "status"]),
            models.Index(fields=["mapping", "status"]),
            models.Index(fields=["buyer_user", "created_at"]),
        ]

    def __str__(self):
        return self.request_no


class SaleMiniProductReview(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        PENDING = "PENDING", "待审核"
        PUBLISHED = "PUBLISHED", "已发布"
        REJECTED = "REJECTED", "已驳回"
        HIDDEN = "HIDDEN", "已隐藏"

    owner = models.ForeignKey(
        Owner,
        verbose_name="货主",
        on_delete=models.PROTECT,
        related_name="sale_mini_product_reviews",
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name="客户",
        on_delete=models.PROTECT,
        related_name="sale_mini_product_reviews",
    )
    buyer_user = models.ForeignKey(
        MiniProgramUser,
        verbose_name="小程序用户",
        on_delete=models.PROTECT,
        related_name="product_reviews",
    )
    mapping = models.ForeignKey(
        SaleMiniOrderMapping,
        verbose_name="商城订单",
        on_delete=models.PROTECT,
        related_name="product_reviews",
    )
    order_line = models.OneToOneField(
        "outbound.OutboundOrderLine",
        verbose_name="出库订单明细",
        on_delete=models.PROTECT,
        related_name="sale_mini_review",
    )
    product = models.ForeignKey(
        Product,
        verbose_name="商品",
        on_delete=models.PROTECT,
        related_name="sale_mini_reviews",
    )
    product_config = models.ForeignKey(
        SaleProductConfig,
        verbose_name="商城商品配置",
        on_delete=models.PROTECT,
        related_name="reviews",
    )
    quality_score = models.PositiveSmallIntegerField(
        "商品质量评分",
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    delivery_score = models.PositiveSmallIntegerField(
        "配送服务评分",
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    overall_score = models.PositiveSmallIntegerField(
        "综合满意度",
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    content = models.TextField("评价内容", max_length=1000, blank=True, default="")
    is_anonymous = models.BooleanField("匿名评价", default=True)
    status = models.CharField(
        "审核状态", max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    rejection_reason = models.CharField("驳回原因", max_length=300, blank=True, default="")
    submitted_at = models.DateTimeField("提交时间", null=True, blank=True)
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        verbose_name="审核人",
        on_delete=models.PROTECT,
        related_name="reviewed_sale_mini_product_reviews",
        null=True,
        blank=True,
    )
    published_at = models.DateTimeField("发布时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城商品评价"
        verbose_name_plural = "商城商品评价"
        ordering = ["-published_at", "-id"]
        indexes = [
            models.Index(fields=["product_config", "status", "published_at"]),
            models.Index(fields=["product", "status", "published_at"]),
            models.Index(fields=["buyer_user", "status", "created_at"]),
            models.Index(fields=["owner", "status", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quality_score__range=(1, 5)),
                name="ck_sale_review_quality_1_5",
            ),
            models.CheckConstraint(
                condition=models.Q(delivery_score__range=(1, 5)),
                name="ck_sale_review_delivery_1_5",
            ),
            models.CheckConstraint(
                condition=models.Q(overall_score__range=(1, 5)),
                name="ck_sale_review_overall_1_5",
            ),
        ]

    def __str__(self):
        return f"review:{self.order_line_id}/{self.get_status_display()}"


class SaleMiniProductReviewImage(BaseModel):
    review = models.ForeignKey(
        SaleMiniProductReview,
        verbose_name="商品评价",
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField("评价图片", upload_to=sale_mini_review_image_path)
    sort_order = models.PositiveSmallIntegerField("排序", default=0)
    size_bytes = models.PositiveIntegerField("文件大小", default=0)
    width = models.PositiveIntegerField("宽度", default=0)
    height = models.PositiveIntegerField("高度", default=0)

    class Meta:
        verbose_name = "商城评价图片"
        verbose_name_plural = "商城评价图片"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["review", "sort_order"],
                name="ux_sale_review_image_order",
            ),
            models.CheckConstraint(
                condition=models.Q(sort_order__lt=6),
                name="ck_sale_review_image_order_lt_6",
            ),
        ]

    def __str__(self):
        return f"review-image:{self.review_id}/{self.sort_order}"


class SaleMiniPaymentEvent(BaseModel):
    class ProcessStatus(models.TextChoices):
        PENDING = "PENDING", "待处理"
        PROCESSED = "PROCESSED", "已处理"
        FAILED = "FAILED", "处理失败"

    event_id = models.CharField("事件编号", max_length=128, unique=True)
    event_type = models.CharField("事件类型", max_length=80)
    resource_type = models.CharField("资源类型", max_length=80, blank=True, default="")
    payment = models.ForeignKey(
        SaleMiniPayment,
        verbose_name="支付单",
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )
    refund = models.ForeignKey(
        SaleMiniRefund,
        verbose_name="退款单",
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )
    out_trade_no = models.CharField("商户支付单号", max_length=64, blank=True, default="")
    out_refund_no = models.CharField("商户退款单号", max_length=64, blank=True, default="")
    payload = models.JSONField("原始回调报文", default=dict, blank=True)
    decrypted_payload = models.JSONField("解密后报文", default=dict, blank=True)
    process_status = models.CharField(
        "处理状态",
        max_length=20,
        choices=ProcessStatus.choices,
        default=ProcessStatus.PENDING,
    )
    error_message = models.CharField("错误信息", max_length=300, blank=True, default="")
    processed_at = models.DateTimeField("处理时间", null=True, blank=True)

    class Meta:
        verbose_name = "商城支付回调事件"
        verbose_name_plural = "商城支付回调事件"
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["out_trade_no"]),
            models.Index(fields=["out_refund_no"]),
            models.Index(fields=["process_status"]),
        ]

    def __str__(self):
        return self.event_id


# —— 订货单位 / 起订量 / 客户商品策略 ——
class CustomerProductPolicy(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="cust_prod_policies")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="product_policies"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="customer_policies")
    order_uom = models.CharField(max_length=32, help_text="订货单位（与商品计量单位体系对齐）")
    min_order_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    multiple_qty = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text="最小增量/倍数，0 表示无限制",
    )

    class Meta:
        unique_together = (("owner", "customer", "product"),)
        indexes = [models.Index(fields=["owner", "customer"])]


# 渠道商品策略（渠道价、起订量、订货单位控制）
class ChannelProductPolicy(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="channel_prod_policies")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="product_policies")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="channel_policies")
    order_uom = models.CharField(max_length=32)
    min_order_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = (("owner", "channel", "product"),)


# —— 价格体系：价目表 + 一店一价 + 价格记忆 ——
class PriceGroup(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="price_groups")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=64)

    class Meta:
        unique_together = (("owner", "code"),)


class PriceList(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="price_lists")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=64)
    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_lists",
    )
    price_group = models.ForeignKey(
        PriceGroup,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="price_lists",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = (("owner", "code"),)
        indexes = [models.Index(fields=["owner", "effective_from", "effective_to"])]


class PriceItem(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="price_items")
    price_list = models.ForeignKey(PriceList, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="price_items")
    price = models.DecimalField(max_digits=12, decimal_places=4)
    currency = models.CharField(max_length=10, default="CNY")

    class Meta:
        unique_together = (("owner", "price_list", "product"),)


# 一店一价（覆盖层）
class CustomerSpecialPrice(BaseModel):
    owner = models.ForeignKey(
        Owner, on_delete=models.PROTECT, related_name="customer_special_prices"
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="special_prices")
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="customer_special_prices"
    )
    special_price = models.DecimalField(max_digits=12, decimal_places=4)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = (("owner", "customer", "product", "effective_from"),)


# 价格记忆（最近成交价）
class PriceMemory(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="price_memories")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="price_memories")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="price_memories")
    last_price = models.DecimalField(max_digits=12, decimal_places=4)
    last_order_date = models.DateField()

    class Meta:
        unique_together = (("owner", "customer", "product"),)


# —— 促销策略：满减/满赠/特价 ——
class Promotion(BaseModel):
    class PromoType(models.TextChoices):
        DISCOUNT_STEP = "discount_step", "满减"
        GIFT = "gift", "满赠"
        SPECIAL_PRICE = "special_price", "特价"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="promotions")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=64)
    promo_type = models.CharField(max_length=32, choices=PromoType.choices)
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    must_select = models.BooleanField(default=False, help_text="是否必选促销")
    min_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        unique_together = (("owner", "code"),)
        indexes = [models.Index(fields=["owner", "promo_type"])]


class PromotionGiftItem(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="promotion_gift_items")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="gift_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    gift_qty = models.DecimalField(max_digits=12, decimal_places=3)


class PromotionDiscountStep(BaseModel):
    owner = models.ForeignKey(
        Owner, on_delete=models.PROTECT, related_name="promotion_discount_steps"
    )
    promotion = models.ForeignKey(
        Promotion, on_delete=models.PROTECT, related_name="discount_steps"
    )
    threshold_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)


class PromotionSpecialPrice(BaseModel):
    owner = models.ForeignKey(
        Owner, on_delete=models.PROTECT, related_name="promotion_special_prices"
    )
    promotion = models.ForeignKey(
        Promotion, on_delete=models.PROTECT, related_name="special_prices"
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    special_price = models.DecimalField(max_digits=12, decimal_places=4)


# —— 访销：拜访计划/签到/轨迹/拍照 ——
class VisitPlan(BaseModel):
    class PlanStatus(models.TextChoices):
        DRAFT = "draft", "草稿"
        APPROVED = "approved", "已批准"
        COMPLETED = "completed", "已完成"
        CANCELED = "canceled", "已取消"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="visit_plans")
    salesperson = models.ForeignKey(
        Salesperson, on_delete=models.PROTECT, related_name="visit_plans"
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="visit_plans")
    planned_date = models.DateField()
    status = models.CharField(max_length=20, choices=PlanStatus.choices, default=PlanStatus.DRAFT)
    route_name = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["owner", "salesperson", "planned_date"])]


class AttendanceRecord(BaseModel):
    class Type(models.TextChoices):
        CHECKIN = "checkin", "签到"
        CHECKOUT = "checkout", "签退"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="attendance_records")
    salesperson = models.ForeignKey(
        Salesperson, on_delete=models.PROTECT, related_name="attendance_records"
    )
    record_type = models.CharField(max_length=20, choices=Type.choices)
    timestamp = models.DateTimeField()
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default="")


class VisitRecord(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="visit_records")
    visit_plan = models.ForeignKey(
        VisitPlan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="records",
    )
    salesperson = models.ForeignKey(
        Salesperson, on_delete=models.PROTECT, related_name="visit_records"
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="visit_records")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    start_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    start_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    end_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    end_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes = models.TextField(blank=True, default="")


class GPSTrackPoint(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="gps_points")
    visit = models.ForeignKey(VisitRecord, on_delete=models.PROTECT, related_name="gps_points")
    timestamp = models.DateTimeField()
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    speed = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)


class PhotoType(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="photo_types")
    name = models.CharField(max_length=50)


class VisitPhoto(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="visit_photos")
    visit = models.ForeignKey(VisitRecord, on_delete=models.PROTECT, related_name="photos")
    photo_type = models.ForeignKey(PhotoType, on_delete=models.PROTECT, null=True, blank=True)
    image = models.ImageField(upload_to="visit_photos/")
    remark = models.CharField(max_length=200, blank=True, default="")


# —— 订单（含退货）、费用垫付与核销、信用控制、应收 ——
class SalesOrder(BaseModel):
    class OrderType(models.TextChoices):
        SALE = "sale", "销售"
        RETURN = "return", "退货"

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        SUBMITTED = "submitted", "已提交"
        APPROVED = "approved", "已审核"
        REJECTED = "rejected", "已驳回"
        FULFILLED = "fulfilled", "已完成"
        CANCELED = "canceled", "已取消"

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="sales_orders")
    org = models.ForeignKey(BizOrg, on_delete=models.PROTECT, null=True, blank=True)
    salesperson = models.ForeignKey(Salesperson, on_delete=models.PROTECT, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders")
    order_type = models.CharField(max_length=10, choices=OrderType.choices, default=OrderType.SALE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField()
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="CNY")
    source = models.CharField(
        max_length=30, default="salesapp", help_text="来源：salesapp/van/portal/…"
    )

    class Meta:
        indexes = [models.Index(fields=["owner", "customer", "status"])]


class SalesOrderLine(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="sales_order_lines")
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    order_uom = models.CharField(max_length=32)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_amount = models.DecimalField(max_digits=14, decimal_places=2)


# 信用策略（业务员+客户双控额度）
class CreditPolicy(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="credit_policies")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="credit_policies")
    salesperson = models.ForeignKey(
        Salesperson,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="credit_policies",
    )
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overdue_days_limit = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["owner", "customer"])]


# 应收台账（可与财务模块对接）
class ARLedger(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="ar_ledgers")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="ar_ledgers")
    ref_order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default="")


# 费用：厂家垫付/费用申请/核销
class ExpenseAdvance(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="expense_advances")
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="expense_advances"
    )
    title = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, default="draft")  # draft/approved/written_off
    remark = models.CharField(max_length=200, blank=True, default="")


class ExpenseWriteOff(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="expense_writeoffs")
    advance = models.ForeignKey(ExpenseAdvance, on_delete=models.PROTECT, related_name="writeoffs")
    writeoff_amount = models.DecimalField(max_digits=14, decimal_places=2)
    writeoff_date = models.DateField()


# 陈列管理（计划/电子协议/拍照/稽核/兑付）
class MerchandisingPlan(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="merch_plans")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="merch_plans")
    title = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    target_shelf = models.IntegerField(default=0)


class MerchandisingAgreement(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="merch_agreements")
    plan = models.ForeignKey(MerchandisingPlan, on_delete=models.PROTECT, related_name="agreements")
    file = models.FileField(upload_to="agreements/")
    signed_at = models.DateField(null=True, blank=True)


class MerchandisingAudit(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="merch_audits")
    plan = models.ForeignKey(MerchandisingPlan, on_delete=models.PROTECT, related_name="audits")
    visit = models.ForeignKey(VisitRecord, on_delete=models.PROTECT, null=True, blank=True)
    result = models.CharField(max_length=50, default="pending")  # pending/pass/fail
    remarks = models.CharField(max_length=200, blank=True, default="")


class RebatePayout(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="rebate_payouts")
    plan = models.ForeignKey(MerchandisingPlan, on_delete=models.PROTECT, null=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="rebate_payouts")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, default="draft")  # draft/approved/paid
