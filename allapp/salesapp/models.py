# salesapp/models.py
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model

from allapp.core.models import BaseModel
from allapp.baseinfo.models import Owner, Customer
from allapp.products.models import Product  # 你现有商品模型
# 如有 Warehouse / UOM 模型，也可在此引入

User = get_user_model()

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
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")

    class Meta:
        unique_together = (("owner", "code"),)
        indexes = [models.Index(fields=["owner", "org_type"])]

# 业务员（可直接复用 User；这里加业务档案）
class Salesperson(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="salespersons")
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="sales_profile")
    org = models.ForeignKey(BizOrg, on_delete=models.PROTECT, null=True, blank=True, related_name="salespersons")
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

# —— 订货单位 / 起订量 / 客户商品策略 ——
class CustomerProductPolicy(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="cust_prod_policies")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="product_policies")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="customer_policies")
    order_uom = models.CharField(max_length=32, help_text="订货单位（与商品计量单位体系对齐）")
    min_order_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    multiple_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="最小增量/倍数，0 表示无限制")

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
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, blank=True, related_name="price_lists")
    price_group = models.ForeignKey(PriceGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="price_lists")
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
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="customer_special_prices")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="special_prices")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="customer_special_prices")
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
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="promotion_discount_steps")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="discount_steps")
    threshold_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)

class PromotionSpecialPrice(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="promotion_special_prices")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="special_prices")
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
    salesperson = models.ForeignKey(Salesperson, on_delete=models.PROTECT, related_name="visit_plans")
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
    salesperson = models.ForeignKey(Salesperson, on_delete=models.PROTECT, related_name="attendance_records")
    record_type = models.CharField(max_length=20, choices=Type.choices)
    timestamp = models.DateTimeField()
    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default="")

class VisitRecord(BaseModel):
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="visit_records")
    visit_plan = models.ForeignKey(VisitPlan, on_delete=models.PROTECT, null=True, blank=True, related_name="records")
    salesperson = models.ForeignKey(Salesperson, on_delete=models.PROTECT, related_name="visit_records")
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
    source = models.CharField(max_length=30, default="salesapp", help_text="来源：salesapp/van/portal/…")

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
    salesperson = models.ForeignKey(Salesperson, on_delete=models.PROTECT, null=True, blank=True, related_name="credit_policies")
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
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="expense_advances")
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
