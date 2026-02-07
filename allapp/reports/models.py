# allapp/reports/models.py
from django.db import models
from django.db.models import Q, F, CheckConstraint, UniqueConstraint
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.conf import settings
# ===========================
# 通用 Mixin
# ===========================
class CreatedMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True

class Scd2Mixin(models.Model):
    """缓慢变化维（SCD2）"""
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(default=True)

    class Meta:
        abstract = True
        constraints = [
            # 区间右开；允许 open-ended
            CheckConstraint(
                check=Q(valid_to__isnull=True) | Q(valid_to__gt=F("valid_from")),
                name="chk_scd2_rng_ok",
            ),
        ]

# ===========================
# 维度表（Dimensions）
# ===========================
class DateDim(CreatedMixin):
    """
    日期主维（自然键为 YYYYMMDD 整数），统一所有日粒度报表的对齐口径。
    """
    date_key = models.IntegerField(primary_key=True)  # e.g. 20250901
    date = models.DateField(unique=True)

    year = models.SmallIntegerField()
    quarter = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    day = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()

    is_month_start = models.BooleanField()
    is_month_end = models.BooleanField()
    is_weekend = models.BooleanField()

    class Meta:
        verbose_name = "日期维"
        verbose_name_plural = "日期维"
        indexes = [
            models.Index(fields=["year", "month"], name="idx_dt_ym"),
        ]

    def __str__(self):
        return f"{self.date_key}"

class OwnerDim(CreatedMixin, Scd2Mixin):
    """
    货主维（SCD2）。仅保留业务主键与必要快照字段，避免直连业务库。
    """
    owner_id = models.BigIntegerField()  # 对应业务库 Owner.pk
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)

    class Meta:
        verbose_name = "货主维(SCD2)"
        verbose_name_plural = "货主维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["owner_id", "valid_from"], name="uq_owner_scd2"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "is_current"], name="idx_owner_cur"),
            models.Index(fields=["code"], name="idx_owner_code"),
        ]

    def __str__(self):
        return f"{self.code}-{self.name}"

class WarehouseDim(CreatedMixin, Scd2Mixin):
    warehouse_id = models.BigIntegerField()
    owner_id = models.BigIntegerField()  # 便于租户裁剪
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=100)
    city = models.CharField(max_length=60, blank=True)
    class Meta:
        verbose_name = "仓库维(SCD2)"
        verbose_name_plural = "仓库维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["warehouse_id", "valid_from"], name="uq_wh_scd2"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "is_current"], name="idx_wh_cur"),
            models.Index(fields=["code"], name="idx_wh_code"),
        ]

class ProductDim(CreatedMixin, Scd2Mixin):
    product_id = models.BigIntegerField()   # 业务库 Product.pk
    owner_id = models.BigIntegerField()
    sku_code = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    category_code = models.CharField(max_length=64, blank=True)
    uom = models.CharField(max_length=16)
    net_weight_kg = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(0)])
    volume_m3 = models.DecimalField(max_digits=12, decimal_places=6, validators=[MinValueValidator(0)])
    shelf_life_days = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "商品维(SCD2)"
        verbose_name_plural = "商品维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["product_id", "valid_from"], name="uq_prod_scd2"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "sku_code"], name="idx_prod_owner_sku"),
            models.Index(fields=["is_current"], name="idx_prod_cur"),
        ]

class CustomerDim(CreatedMixin, Scd2Mixin):
    customer_id = models.BigIntegerField()
    owner_id = models.BigIntegerField()
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    level = models.CharField(max_length=40, blank=True)

    class Meta:
        verbose_name = "客户维(SCD2)"
        verbose_name_plural = "客户维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["customer_id", "valid_from"], name="uq_cust_scd2"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "is_current"], name="idx_cust_cur"),
            models.Index(fields=["code"], name="idx_cust_code"),
        ]

class SupplierDim(CreatedMixin, Scd2Mixin):
    supplier_id = models.BigIntegerField()
    owner_id = models.BigIntegerField()
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    class Meta:
        verbose_name = "供应商维(SCD2)"
        verbose_name_plural = "供应商维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["supplier_id", "valid_from"], name="uq_supp_scd2"),
        ]
        indexes = [
            models.Index(fields=["owner_id", "is_current"], name="idx_supp_cur"),
            models.Index(fields=["code"], name="idx_supp_code"),
        ]

class CarrierDim(CreatedMixin, Scd2Mixin):
    carrier_id = models.BigIntegerField()
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    class Meta:
        verbose_name = "承运商维(SCD2)"
        verbose_name_plural = "承运商维(SCD2)"
        constraints = [
            UniqueConstraint(fields=["carrier_id", "valid_from"], name="uq_car_scd2"),
        ]
        indexes = [
            models.Index(fields=["code"], name="idx_car_code"),
        ]

class ReasonDim(CreatedMixin):
    """异常/调整原因码（稳定维）"""
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    class Meta:
        verbose_name = "原因码维"
        verbose_name_plural = "原因码维"

class TempZoneDim(CreatedMixin):
    """温度带维（稳定维）"""
    code = models.CharField(max_length=20, unique=True)   # e.g. AMB/CHL/FRZ
    name = models.CharField(max_length=60)
    class Meta:
        verbose_name = "温度带维"
        verbose_name_plural = "温度带维"

# ===========================
# 事实表（Facts）
# ===========================
class FactInventorySnapshotDaily(models.Model):
    """
    库存日快照：按日 owner/warehouse/location/product/lot 聚合的库存状态。
    """
    snapshot_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)
    location_id = models.BigIntegerField()                 # 不建维，直接存业务库 location.pk
    product = models.ForeignKey(ProductDim, on_delete=models.PROTECT)
    lot_no = models.CharField(max_length=60, blank=True)

    qty_onhand = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_alloc = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_available = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_damage = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    qty_expired = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    amount_value = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "库存日快照"
        verbose_name_plural = "库存日快照"
        constraints = [
            UniqueConstraint(
                fields=["snapshot_date", "owner", "warehouse", "location_id", "product", "lot_no"],
                name="uq_inv_snap_key",
            ),
            CheckConstraint(
                check=Q(qty_onhand__gte=0) & Q(qty_alloc__gte=0) & Q(qty_available__gte=0) &
                      Q(qty_damage__gte=0) & Q(qty_expired__gte=0) & Q(amount_value__gte=0),
                name="chk_inv_snap_nn",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "snapshot_date"], name="idx_inv_owner_date"),
            models.Index(fields=["warehouse", "snapshot_date"], name="idx_inv_wh_date"),
            models.Index(fields=["product", "snapshot_date"], name="idx_inv_prod_date"),
        ]

class FactInventoryTxn(models.Model):
    """
    库存交易流水：来自业务库 InventoryTransaction（增量抽取）
    """
    txn_id = models.BigIntegerField(unique=True)  # 业务库交易ID，保证幂等
    occurred_at = models.DateTimeField(db_index=True)

    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)
    location_id = models.BigIntegerField(null=True, blank=True)
    product = models.ForeignKey(ProductDim, on_delete=models.PROTECT)
    lot_no = models.CharField(max_length=60, blank=True)
    reason = models.ForeignKey(ReasonDim, on_delete=models.PROTECT, null=True, blank=True)

    order_type = models.CharField(max_length=20)            # INBOUND/OUTBOUND/TRANSFER/COUNT/ADJUST...
    order_id = models.BigIntegerField(null=True, blank=True)

    qty_delta = models.DecimalField(max_digits=18, decimal_places=3)     # 可为负
    amount_delta = models.DecimalField(max_digits=18, decimal_places=2)  # 可为负

    class Meta:
        verbose_name = "库存交易事实"
        verbose_name_plural = "库存交易事实"
        constraints = [
            CheckConstraint(
                check=~(Q(qty_delta=0) & Q(amount_delta=0)),
                name="chk_txn_nonzero",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "occurred_at"], name="idx_txn_owner_time"),
            models.Index(fields=["warehouse", "occurred_at"], name="idx_txn_wh_time"),
            models.Index(fields=["product", "occurred_at"], name="idx_txn_prod_time"),
            models.Index(fields=["order_type", "occurred_at"], name="idx_txn_otype_time"),
        ]

class FactInboundLine(models.Model):
    """
    入库事实：按入库明细行对齐（计划/实收/拒收/破损、时效）
    """
    line_id = models.BigIntegerField(unique=True)  # 业务库 InboundOrderLine.pk
    order_id = models.BigIntegerField()
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)
    supplier = models.ForeignKey(SupplierDim, on_delete=models.PROTECT, null=True, blank=True)
    product = models.ForeignKey(ProductDim, on_delete=models.PROTECT)

    order_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, related_name="ib_order_dt", to_field="date_key")
    receive_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, related_name="ib_recv_dt", to_field="date_key", null=True, blank=True)
    putaway_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, related_name="ib_put_dt", to_field="date_key", null=True, blank=True)

    qty_plan = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_received = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_reject = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    qty_damage = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])

    sec_to_receive = models.IntegerField(null=True, blank=True)  # 到仓->收货完成
    sec_to_putaway = models.IntegerField(null=True, blank=True)  # 收货->上架完成

    class Meta:
        verbose_name = "入库行事实"
        verbose_name_plural = "入库行事实"
        indexes = [
            models.Index(fields=["owner", "order_date"], name="idx_ib_owner_date"),
            models.Index(fields=["warehouse", "receive_date"], name="idx_ib_wh_recv"),
            models.Index(fields=["product", "order_date"], name="idx_ib_prod_date"),
        ]

class FactOutboundLine(models.Model):
    """
    出库事实：按出库明细行对齐（计划/分配/拣/包/发货、OTIF）
    """
    line_id = models.BigIntegerField(unique=True)  # 业务库 OutboundOrderLine.pk
    order_id = models.BigIntegerField()
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)
    customer = models.ForeignKey(CustomerDim, on_delete=models.PROTECT, null=True, blank=True)
    product = models.ForeignKey(ProductDim, on_delete=models.PROTECT)

    order_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, related_name="ob_order_dt", to_field="date_key")
    ship_date = models.ForeignKey(DateDim, on_delete=models.PROTECT, related_name="ob_ship_dt", to_field="date_key", null=True, blank=True)

    qty_plan = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_alloc = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_picked = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_packed = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])
    qty_shipped = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(0)])

    sec_alloc = models.IntegerField(null=True, blank=True)   # 下单->分配
    sec_pick = models.IntegerField(null=True, blank=True)    # 分配->拣货完成
    sec_pack = models.IntegerField(null=True, blank=True)    # 拣->包
    sec_ship = models.IntegerField(null=True, blank=True)    # 包->发货

    in_full = models.BooleanField(default=False)             # 齐套
    on_time = models.BooleanField(default=False)             # 准时（按你的SLA定义）

    class Meta:
        verbose_name = "出库行事实"
        verbose_name_plural = "出库行事实"
        indexes = [
            models.Index(fields=["owner", "order_date"], name="idx_ob_owner_date"),
            models.Index(fields=["warehouse", "ship_date"], name="idx_ob_wh_ship"),
            models.Index(fields=["product", "order_date"], name="idx_ob_prod_date"),
            models.Index(fields=["customer", "ship_date"], name="idx_ob_cust_ship"),
        ]

class FactBilling(models.Model):
    """
    计费事实（来自 BillingDailyRecord 与各类 Fee 明细汇总对齐的行）
    """
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT, null=True, blank=True)
    date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")

    fee_type = models.CharField(max_length=30)  # STORAGE/OPERATION/DELIVERY/OUTBOUND_VALUE...
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0)])

    # 幂等去重键（可选：owner+业务事件ID的哈希）
    dedup_key = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = "计费事实"
        verbose_name_plural = "计费事实"
        constraints = [
            CheckConstraint(check=Q(amount__gte=0), name="chk_fee_amt_ge0"),
        ]
        indexes = [
            models.Index(fields=["owner", "date"], name="idx_fee_owner_date"),
            models.Index(fields=["fee_type", "date"], name="idx_fee_type_date"),
            models.Index(fields=["dedup_key"], name="idx_fee_dedup"),
        ]

# ===========================
# 预聚合（物化汇总）
# ===========================
class AggThroughputDaily(models.Model):
    """吞吐：按日 owner/wh 的入出库行/件汇总"""
    date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)

    inbound_lines = models.IntegerField(default=0)
    inbound_qty = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    outbound_lines = models.IntegerField(default=0)
    outbound_qty = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "吞吐日汇总"
        verbose_name_plural = "吞吐日汇总"
        constraints = [
            UniqueConstraint(fields=["date", "owner", "warehouse"], name="uq_tput_key"),
            CheckConstraint(check=Q(inbound_lines__gte=0) & Q(outbound_lines__gte=0), name="chk_tput_cnt_ge0"),
        ]
        indexes = [
            models.Index(fields=["owner", "date"], name="idx_tput_owner_date"),
            models.Index(fields=["warehouse", "date"], name="idx_tput_wh_date"),
        ]

class AggOTIFDaily(models.Model):
    """OTIF（按 owner/customer/date 的准时率、齐全率）"""
    date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    customer = models.ForeignKey(CustomerDim, on_delete=models.PROTECT)

    orders = models.IntegerField(default=0)
    orders_on_time = models.IntegerField(default=0)
    orders_in_full = models.IntegerField(default=0)

    class Meta:
        verbose_name = "OTIF日汇总"
        verbose_name_plural = "OTIF日汇总"
        constraints = [
            UniqueConstraint(fields=["date", "owner", "customer"], name="uq_otif_key"),
            CheckConstraint(
                check=Q(orders__gte=0) & Q(orders_on_time__gte=0) & Q(orders_in_full__gte=0),
                name="chk_otif_ge0",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "date"], name="idx_otif_owner_date"),
            models.Index(fields=["customer", "date"], name="idx_otif_cust_date"),
        ]

class AggInventoryAging(models.Model):
    """库龄（按 owner/wh/sku 的库龄段数量/金额）"""
    date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT)
    product = models.ForeignKey(ProductDim, on_delete=models.PROTECT)

    band = models.CharField(max_length=20)  # e.g. "0-7","8-15","16-30","31-60","61-90","90+"
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "库龄汇总"
        verbose_name_plural = "库龄汇总"
        constraints = [
            UniqueConstraint(fields=["date", "owner", "warehouse", "product", "band"], name="uq_age_key"),
        ]
        indexes = [
            models.Index(fields=["owner", "date"], name="idx_age_owner_date"),
            models.Index(fields=["warehouse", "date"], name="idx_age_wh_date"),
            models.Index(fields=["product", "date"], name="idx_age_prod_date"),
        ]

class AggBillingDaily(models.Model):
    """计费按日汇总（对齐账单口径）"""
    date = models.ForeignKey(DateDim, on_delete=models.PROTECT, to_field="date_key")
    owner = models.ForeignKey(OwnerDim, on_delete=models.PROTECT)
    warehouse = models.ForeignKey(WarehouseDim, on_delete=models.PROTECT, null=True, blank=True)
    fee_type = models.CharField(max_length=30)

    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(0)])
    class Meta:
        verbose_name = "计费日汇总"
        verbose_name_plural = "计费日汇总"
        constraints = [
            UniqueConstraint(fields=["date", "owner", "warehouse", "fee_type"], name="uq_bill_key"),
        ]
        indexes = [
            models.Index(fields=["owner", "date"], name="idx_bill_owner_date"),
            models.Index(fields=["fee_type", "date"], name="idx_bill_type_date"),
        ]

# ===========================
# ETL / 数据治理元数据
# ===========================
class EtlWatermark(models.Model):
    """
    增量抽取水位：存放各域的最后同步点（时间戳或自增ID）
    """
    domain = models.CharField(max_length=40, unique=True)  # e.g. 'inbound_line', 'inventory_txn'
    watermark_value = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ETL水位"
        verbose_name_plural = "ETL水位"

class EtlJobRun(models.Model):
    """
    ETL 任务运行日志：起止时间、行数、状态、错误
    """
    job_name = models.CharField(max_length=80)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    rows_in = models.IntegerField(default=0)
    rows_out = models.IntegerField(default=0)
    error = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "ETL运行日志"
        verbose_name_plural = "ETL运行日志"
        indexes = [
            models.Index(fields=["job_name", "started_at"], name="idx_job_name_time"),
        ]

class DedupLedger(models.Model):
    """
    幂等去重台账：对跨域唯一键建立指纹。
    """
    domain = models.CharField(max_length=40)            # e.g. 'fact_billing'
    dedup_key = models.CharField(max_length=120)        # 业务唯一键或其哈希
    occurred_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "幂等去重点"
        verbose_name_plural = "幂等去重点"
        constraints = [
            UniqueConstraint(fields=["domain", "dedup_key"], name="uq_dedup_key"),
        ]
        indexes = [
            models.Index(fields=["domain", "occurred_date"], name="idx_dedup_dom_date"),
        ]

class ReportSnapshot(models.Model):
    """通用报表快照：冻结渲染上下文与关键抬头字段。
    可用于配送单、收货单、盘点单等。"""
    owner = models.ForeignKey("baseinfo.Owner", verbose_name="货主", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("locations.Warehouse", verbose_name="出库", on_delete=models.PROTECT,editable=False,default=settings.DEFAULT_WAREHOUSE_ID)


    src_model = models.CharField("来源模型", max_length=40) # e.g. "WmsTask"
    src_id = models.BigIntegerField("来源ID")
    doc_type = models.CharField("单据类型", max_length=30) # e.g. "DISPATCH_NOTE"
    doc_no = models.CharField("单号(快照)", max_length=40, blank=True, default="")


    template = models.CharField("模板键", max_length=40, default="dispatch_note")
    tpl_ver = models.CharField("模板版本", max_length=10, default="v1")


    payload = models.JSONField("渲染数据快照") # 存 header/items 等
    html = models.TextField("HTML快照", blank=True, default="")
    amount_total= models.DecimalField("金额合计", max_digits=18, decimal_places=2, default=0)
    amount_upper= models.CharField("金额大写", max_length=60, blank=True, default="")


    fp = models.CharField("指纹", max_length=64, unique=True) # sha256(payload+tpl_ver)
    is_final = models.BooleanField("是否定稿", default=False)


    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


    class Meta:
        verbose_name = "报表快照"
        verbose_name_plural = "报表快照"
        constraints = [
        models.CheckConstraint(name="ck_rpt_amt_nonneg", check=models.Q(amount_total__gte=0)),
        ]
        indexes = [
        models.Index(fields=["doc_type", "doc_no"], name="ix_rpt_doctype_no"),
        models.Index(fields=["owner", "warehouse", "created_at"], name="ix_rpt_own_wh_time"),
        ]

    def __str__(self):
        return f"{self.doc_type}:{self.doc_no}#{self.id}"