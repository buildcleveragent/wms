# allapp/inventory/models.py
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.db.models import Q, F, CheckConstraint, Value,BooleanField
import datetime
from django.db.models.functions import Upper, Coalesce, NullIf
from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator
from allapp.core.choices import ZoneType
from allapp.core.models import BaseModel
from allapp.core.choices import (SubmitStatus, InvTxType, TransferStatus)
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse, Subwarehouse, Location
from allapp.products.models import Product
# =========================A. 现存量 =========================
class InventoryDetail(BaseModel):
    """
    现存量快照（Django 5.x + MySQL 8.0.43）
    维度：owner + product + warehouse + zone(可空) + location + 批次/效期 + (可选) serial_no
    数量全部以 product.base_uom 计；base_unit 为冗余快照（禁止手改）。
    """

    # —— 维度外键 —— #
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, verbose_name="货主")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, verbose_name="商品")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, verbose_name="仓库")
    subwarehouse = models.ForeignKey("locations.Subwarehouse", on_delete=models.PROTECT, verbose_name="仓库",null=True, blank=True,)
    zone_type = models.PositiveSmallIntegerField(
        _("区域类型"), choices=ZoneType.choices, default=ZoneType.STORAGE, db_index=True
    )
    location = models.ForeignKey("locations.Location", on_delete=models.PROTECT, verbose_name="库位")

    # （可选但强烈建议）批次外键；仍保留批次快照字段方便打印/兼容
    lot = models.ForeignKey("inbound.Lot", on_delete=models.PROTECT, null=True, blank=True, verbose_name="批次")

    # —— 批次/效期/序列（字符串统一：写入端自行规范；唯一约束里再兜底大小写）—— #
    batch_no = models.CharField("批次号", max_length=64, blank=True, default="")
    production_date = models.DateField("生产日期", null=True, blank=True)
    expiry_date = models.DateField("有效期至", null=True, blank=True)

    serial_no = models.CharField("序列号", max_length=64, blank=True, default="")
    # 规范化后的序列号：空/空白 -> NULL；非空 -> UPPER，供“跨位置唯一”约束与检索
    serial_no_norm = models.CharField("序列号(规范化)", max_length=64, null=True, blank=True, db_index=True)

    # —— 冗余快照 —— #
    base_unit = models.CharField("基本单位", max_length=30)
    product_serial_control = models.BooleanField("序列化控制快照", default=False, )

    # —— 数量 —— #
    onhand_qty =    models.DecimalField("账面库存", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    allocated_qty = models.DecimalField("已分配数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    locked_qty =    models.DecimalField("锁定数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    damaged_qty =   models.DecimalField("损坏数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    # 可用量存储，但由 save() 恒等式强制回填
    available_qty = models.DecimalField("可用数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "库存现存量"
        verbose_name_plural = "库存现存量"

        # —— 唯一/检查约束（MySQL 8.0.13+ 支持表达式唯一/函数索引） —— #
        constraints = [
            # 1) 维度唯一：仅允许“1条活跃快照”

            #    - batch/serial：大小写不敏感；NULL→""（用于唯一判断）
            #    - production/expiry：NULL→'1000-01-01'（用于唯一判断）

            models.UniqueConstraint(
                F("owner"),
                F("product"),
                F("warehouse"),
                F("location"),
                Upper(Coalesce(F("batch_no"), Value(""))),
                Coalesce(F("production_date"), Value(datetime.date(1000, 1, 1))),
                Coalesce(F("expiry_date"), Value(datetime.date(1000, 1, 1))),
                Upper(Coalesce(F("serial_no"), Value(""))),
                F("is_active"),  # 若模型没有 is_active 字段，请删掉这一项
                name="ux_inv_dim_active_mysql",
            ),
            # 2) 数量非负
            models.CheckConstraint(
                name="chk_inv_non_negative",
                check=Q(onhand_qty__gte=0, allocated_qty__gte=0,
                        locked_qty__gte=0, damaged_qty__gte=0, available_qty__gte=0),
            ),

            # 3) 序列化商品数量限制（更严格可改为 in {0,1}）
            models.CheckConstraint(
                name="chk_inv_serial_qty_le_one",
                check=Q(product_serial_control=False) |
                      Q(onhand_qty__lte=1, allocated_qty__lte=1,
                        locked_qty__lte=1, damaged_qty__lte=1, available_qty__lte=1),
            ),

            # 4) 可用量恒等式：available = onhand - allocated - locked - damaged
            models.CheckConstraint(
                name="chk_inv_available_identity",
                check=Q(available_qty=F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty")),
            ),

            # 5) 序列号跨位置唯一（仅对“非空规范化 SN”生效；NULL 可重复）
            models.UniqueConstraint(
                fields=["owner", "product", "serial_no_norm", "is_active"],  # 若无 is_active，可去掉
                name="ux_inv_serial_owner_prod_act",
            ),
        ]

        # —— 索引 —— #
        indexes = [
            # FEFO：到期为空则推到最后
            models.Index(
                F("owner"), F("product"), F("warehouse"), F("location"),
                Coalesce(F("expiry_date"), Value(datetime.date(9999, 12, 31))),
                name="idx_inv_fefo_mysql",
            ),

            # 批次检索（批次不区分大小写）
            models.Index(
                F("owner"), F("product"),
                Upper(Coalesce(F("batch_no"), Value(""))),
                name="idx_inv_owner_prod_batch_mysql",
            ),

            # 有效期检索
            models.Index(
                F("owner"), F("product"),
                Coalesce(F("expiry_date"), Value(datetime.date(1000, 1, 1))),
                name="idx_inv_owner_prod_exp",
            ),

            # 位置+商品（这是“字段索引”，仍然用 fields=）
            models.Index(name="idx_inv_loc_product_mysql", fields=["warehouse", "subwarehouse","location", "product"]),

            # 活跃过滤
            models.Index(name="idx_inv_is_active", fields=["is_active"]),

        ]

    def __str__(self):
        return f"INV[{self.owner_id}/{self.warehouse_id}/{self.location_id}/{self.product_id}]"

    def _sync_scope_from_location(self):
        if self.location_id and not self.warehouse_id:
            self.warehouse_id = self.location.warehouse_id

    # —— 业务校验：保存前标准化 + 一致性 —— #
    def clean(self):
        self._sync_scope_from_location()
        errors = {}

        # 位置一致性（跨表只能在应用层校验）
        if self.location_id:
            if self.warehouse_id and self.warehouse_id != self.location.warehouse_id:
                errors["warehouse"] = "warehouse 必须与 location.warehouse 一致。"
            if self.subwarehouse_id and self.subwarehouse_id != self.location.subwarehouse_id:
                errors["subwarehouse"] = "subwarehouse 必须与 location.subwarehouse 一致。"

        # 产品驱动的控制口径
        if self.product_id:
            p = self.product

            # 基本单位快照
            if getattr(p, "base_uom_id", None):
                self.base_unit = p.base_uom.code

            # 序列化控制（统一用 serial_control）
            is_serial = bool(getattr(p, "serial_control", False))
            self.product_serial_control = is_serial

            # 规范化序列号：空/空白 -> ""；规范化列 -> NULL
            s = (self.serial_no or "").strip().upper()
            if is_serial:
                if not s:
                    errors["serial_no"] = "序列号管理商品必须填写序列号。"
                self.serial_no = s
                self.serial_no_norm = s  # 非空
            else:
                # if s:
                #     errors["serial_no"] = "该商品未启用序列号管理，序列号必须留空。"
                self.serial_no = ""
                self.serial_no_norm = None  # 允许多行 NULL

            # 批次控制
            if bool(getattr(p, "batch_control", False)):
                self.batch_no = (self.batch_no or "").strip().upper()
            # else:
            #     # if self.batch_no:
            #     #     errors["batch_no"] = "该商品未启用批次管理，批次号必须留空。"
            #     # self.batch_no = ""

            # 效期控制
            if bool(getattr(p, "expiry_control", False)):
                # 允许 production/expiry 为 NULL；若有值，可加 exp >= prod 的校验
                if self.production_date and self.expiry_date and self.expiry_date < self.production_date:
                    errors["expiry_date"] = "有效期不得早于生产日期。"
            # else:
            #     if self.production_date or self.expiry_date:
            #         errors["expiry_date"] = "该商品未启用效期管理，生产/到期日必须留空。"
            #     self.production_date = None
            #     self.expiry_date = None

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._sync_scope_from_location()

        # —— 回填快照 —— #
        if self.product_id and getattr(self.product, "base_uom_id", None):
            self.base_unit = self.product.base_uom.code
            self.product_serial_control = bool(getattr(self.product, "serial_control", False))

        # —— 可用量恒等式 —— #
        self.available_qty = (self.onhand_qty or Decimal("0")) \
                             - (self.allocated_qty or Decimal("0")) \
                             - (self.locked_qty or Decimal("0")) \
                             - (self.damaged_qty or Decimal("0"))

        # —— 规范化序列号（与 clean 保持一致；避免绕过 clean 的直接 save）—— #
        s = (self.serial_no or "").strip().upper()
        if self.product_serial_control:
            self.serial_no = s
            self.serial_no_norm = s or None
        else:
            self.serial_no = ""
            self.serial_no_norm = None

        # 严格校验
        self.full_clean()
        return super().save(*args, **kwargs)

    # —— FEFO 辅助（业务层排序用，不入库） —— #
    @property
    def fefo_key(self):
        return self.expiry_date or datetime.date(9999, 12, 31)

    # def validate_constraints(self, exclude=None):
    #     """
    #     仅跳过“包含表达式”的 UniqueConstraint，让 DB 执行约束；
    #     其他约束（普通 Unique、Check）仍由 Django 预校验。
    #     """
    #     errors = {}
    #     using = self._state.db or DEFAULT_DB_ALIAS
    #
    #     for c in self._meta.constraints:
    #         # 跳过表达式版 UniqueConstraint（contains_expressions=True）
    #         if isinstance(c, models.UniqueConstraint) and getattr(c, "contains_expressions", False):
    #             continue
    #         try:
    #             c.validate(model=self.__class__, instance=self, exclude=exclude, using=using)
    #         except ValidationError as e:
    #             # 累加错误
    #             for k, v in e.error_dict.items():
    #                 errors.setdefault(k, []).extend(v)
    #
    #     if errors:
    #         raise ValidationError(errors)

class InventorySnapshotDaily(models.Model):
    snapshot_date = models.DateField("快照日期", db_index=True)
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, verbose_name="货主")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, verbose_name="仓库")
    location = models.ForeignKey("locations.Location", on_delete=models.PROTECT, verbose_name="库位")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, verbose_name="商品")

    batch_no = models.CharField("批次号", max_length=64, blank=True, default="")
    production_date = models.DateField("生产日期", null=True, blank=True)
    expiry_date = models.DateField("有效期至", null=True, blank=True)
    serial_no = models.CharField("序列号", max_length=64, blank=True, default="")

    onhand_qty = models.DecimalField("账面库存快照", max_digits=18, decimal_places=4, default=0)
    available_qty = models.DecimalField("可用库存快照", max_digits=18, decimal_places=4, default=0)
    allocated_qty = models.DecimalField("已分配快照", max_digits=18, decimal_places=4, default=0)
    locked_qty = models.DecimalField("锁定快照", max_digits=18, decimal_places=4, default=0)
    damaged_qty = models.DecimalField("损坏快照", max_digits=18, decimal_places=4, default=0)

    unit_volume_m3_snapshot = models.DecimalField("单位体积快照(m³)", max_digits=12, decimal_places=6, null=True, blank=True)
    location_area_m2_snapshot = models.DecimalField("库位面积快照(㎡)", max_digits=12, decimal_places=4, null=True, blank=True)
    snapshot_source = models.CharField("快照来源", max_length=40, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "库存日快照"
        verbose_name_plural = "库存日快照"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "snapshot_date",
                    "owner",
                    "warehouse",
                    "location",
                    "product",
                    "batch_no",
                    "production_date",
                    "expiry_date",
                    "serial_no",
                ],
                name="ux_inv_snapshot_daily_dim",
            ),
        ]
        indexes = [
            models.Index(fields=["snapshot_date", "owner", "warehouse"], name="idx_inv_snapshot_date_scope"),
            models.Index(fields=["owner", "warehouse", "location"], name="idx_inv_snapshot_scope_loc"),
            models.Index(fields=["product", "snapshot_date"], name="idx_inv_snapshot_product_date"),
        ]

    def __str__(self):
        return f"SNAP[{self.snapshot_date}][{self.owner_id}/{self.warehouse_id}/{self.location_id}/{self.product_id}]"

# =========================B. 汇总（Owner+SKU）=========================
class InventorySummary(BaseModel):
    """
    汇总：按 (owner + product) 粒度的“库存汇总快照”。
    - 同一 (owner, product) 仅允许 1 条“活跃”记录，历史记录不限。
    - 数量非负，available = onhand - allocated - locked - damaged。
    - base_unit 为 Product 基本单位快照（系统维护，只读）。
    """
    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, verbose_name="货主")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, verbose_name="商品")

    base_unit = models.CharField("基本单位", max_length=30, )

    onhand_qty    = models.DecimalField("账面库存",   max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    allocated_qty = models.DecimalField("已分配数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    locked_qty    = models.DecimalField("锁定数量",   max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    damaged_qty   = models.DecimalField("损坏数量",   max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])
    available_qty = models.DecimalField("可用数量", max_digits=18, decimal_places=4, default=0,validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "库存汇总"
        verbose_name_plural = "库存汇总"

        constraints = [
            # 只对“活跃”记录施加唯一：False(0)->NULL 可重复，True(1)->1 必须唯一
            models.UniqueConstraint(
                F("owner"),
                F("product"),
                # NullIf(F("is_active"), Value(0)),
                NullIf(F("is_active"), Value(False), output_field=BooleanField()),
                name="ux_sum_owner_prod_active_only",
            ),
            # 数量非负
            models.CheckConstraint(
                check=Q(onhand_qty__gte=0) &
                      Q(available_qty__gte=0) &
                      Q(allocated_qty__gte=0) &
                      Q(locked_qty__gte=0) &
                      Q(damaged_qty__gte=0),
                name="chk_invsummary_non_negative",
            ),
            # 恒等式：available = onhand - allocated - locked - damaged
            models.CheckConstraint(
                check=Q(
                    available_qty=F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty")
                ),
                name="chk_sum_avail_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "product"], name="idx_summary_owner_product"),
            models.Index(fields=["is_active"], name="idx_summary_is_active"),
        ]

    # —— 口径统一 —— #
    def clean(self):
        # base_unit 快照
        if self.product_id and getattr(self.product, "base_uom_id", None):
            self.base_unit = self.product.base_uom.code

        # 语义校验（避免 available 为负）
        calc_available = (self.onhand_qty or Decimal("0")) \
                         - (self.allocated_qty or Decimal("0")) \
                         - (self.locked_qty or Decimal("0")) \
                         - (self.damaged_qty or Decimal("0"))
        if calc_available < 0:
            raise ValidationError({"available_qty": "可用数量不可为负，请检查分配/锁定/损坏是否超出账面。"})

    def save(self, *args, **kwargs):
        # 1) base_unit 快照
        if self.product_id and getattr(self.product, "base_uom_id", None):
            self.base_unit = self.product.base_uom.code

        # 2) 恒等式
        self.available_qty = (self.onhand_qty or Decimal("0")) \
                             - (self.allocated_qty or Decimal("0")) \
                             - (self.locked_qty or Decimal("0")) \
                             - (self.damaged_qty or Decimal("0"))

        # 3) 严格校验
        self.full_clean()
        return super().save(*args, **kwargs)

# =========================C. 库存事务流水=========================
# 建议统一枚举（示例）
class InventoryTransaction(BaseModel):
    """
    单库位分录：
    - RECEIVE/ISSUE：location 必填；qty_delta >0 入、<0 出。
    - 移库：两条分录（ISSUE + RECEIVE），用 pair_id 关联。
    - 数量按 Product.base_uom 计。
    """

    tx_type   = models.CharField("事务类型", max_length=16, choices=InvTxType.choices)

    owner     = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, verbose_name="货主")
    product   = models.ForeignKey("products.Product", on_delete=models.PROTECT, verbose_name="商品")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, verbose_name="仓库")
    location  = models.ForeignKey("locations.Location", on_delete=models.PROTECT, verbose_name="库位")
    subwarehouse = models.ForeignKey("locations.Subwarehouse", on_delete=models.PROTECT, verbose_name="仓库", null=True,
                                     blank=True, )
    zone_type = models.PositiveSmallIntegerField(
        _("区域类型"), choices=ZoneType.choices, default=ZoneType.STORAGE, db_index=True
    )
    batch_no        = models.CharField("批次号", max_length=64, blank=True, default="")
    production_date = models.DateField("生产日期", null=True, blank=True)
    expiry_date     = models.DateField("有效期至", null=True, blank=True)
    serial_no       = models.CharField("序列号", max_length=64, blank=True, default="")

    base_unit = models.CharField("基本单位", max_length=30, editable=False)
    qty_delta = models.DecimalField("数量变化(+入/-出)", max_digits=18, decimal_places=4)

    # 同一次移库的成对标识（仅 ISSUE/RECEIVE 使用；可为空）
    pair_id  = models.UUIDField("配对号", null=True, blank=True, db_index=True)

    # 来源单据快照（用作幂等/追溯）
    src_model   = models.CharField("来源类型", max_length=64)
    src_id      = models.BigIntegerField("来源ID")
    src_line_id = models.BigIntegerField("来源行ID", null=True, blank=True)
    src_no      = models.CharField("来源单号", max_length=64, blank=True, default="")

    memo = models.CharField("备注", max_length=255, blank=True, default="")

    # ✅ 新增：过账生效时间 / 过账批次号
    posted_at = models.DateTimeField("过账时间", null=True, blank=True, db_index=True)
    posting_batch = models.CharField("过账批次号", max_length=40, null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "库存事务流水"
        verbose_name_plural = "库存事务流水"
        indexes = [
            models.Index(fields=["tx_type", "owner", "product"], name="idx_tx_type_owner_product"),
            models.Index(fields=["warehouse", "location"], name="idx_tx_wh_loc"),
            models.Index(fields=["owner", "product", "batch_no"], name="idx_tx_batch"),
            models.Index(fields=["owner", "product", "expiry_date"], name="idx_tx_expiry"),
            models.Index(fields=["owner", "product", "serial_no"], name="idx_tx_serial"),
            models.Index(fields=["src_model", "src_id"], name="idx_tx_src"),
            models.Index(fields=["pair_id"], name="idx_tx_pair"),
            # ✅ 可选：常用的 posted_at 窗口查询与按批次定位，已在字段上 db_index=True，这里不再重复
            models.Index(fields=["posted_at"], name="idx_tx_posted_at"),
            models.Index(fields=["posting_batch"], name="idx_tx_posting_batch"),
        ]
        constraints = [
            # location 必填
            CheckConstraint(name="ck_tx_location_required", check=Q(location__isnull=False)),
            # qty_delta 非 0
            CheckConstraint(name="ck_tx_qty_non_zero", check=~Q(qty_delta=0)),
            # RECEIVE >0，ISSUE <0
            CheckConstraint(
                name="ck_tx_sign_by_type",
                check=(
                    (Q(tx_type=InvTxType.RECEIVE) & Q(qty_delta__gt=0)) |
                    (Q(tx_type=InvTxType.ISSUE)   & Q(qty_delta__lt=0)) |
                    Q(tx_type__in=[InvTxType.ADJ_GAIN, InvTxType.ADJ_LOSS])  # 调整由业务控制正负
                ),
            ),
            # 幂等（如需允许同一来源行多分录，请改为指纹 fp 唯一）
            models.UniqueConstraint(
                fields=["src_model", "src_id", "src_line_id", "tx_type"],
                name="ux_tx_src_unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.location_id and not self.warehouse_id:
            self.warehouse_id = self.location.warehouse_id

        # 1) base_unit 回填
        if self.product_id and getattr(self.product, "base_uom_id", None):
            self.base_unit = self.product.base_uom.code

        # 2) 归一化字符串
        self.batch_no = (self.batch_no or "").strip().upper()
        self.serial_no = (self.serial_no or "").strip().upper()

        # 3) 商品维度一致性
        p = getattr(self, "product", None)
        if p:
            if getattr(p, "serial_control", False):
                if not self.serial_no:
                    raise ValidationError({"serial_no": "序列号管理商品必须填写序列号"})
                # 如需强约束：abs(qty_delta)==1，可在此加校验
            # else:
            #     if self.serial_no:
            #         raise ValidationError({"serial_no": "未启用序列号管1理，序列号必须留空"})

            # if not getattr(p, "batch_control", False) and self.batch_no:
            #     raise ValidationError({"batch_no": "未启用批次管理，批次号必须留空"})
            # if not getattr(p, "expiry_control", False) and (self.production_date or self.expiry_date):
            #     raise ValidationError({"expiry_date": "未启用效期管1理，生产/到期日必须留空"})

        # 4) 仓库一致性
        if self.location_id and self.location.warehouse_id != self.warehouse_id:
            raise ValidationError({"warehouse": "location 必须隶属 warehouse"})

        # 5) pair_id 使用范围
        if self.pair_id and self.tx_type not in (InvTxType.RECEIVE, InvTxType.ISSUE):
            raise ValidationError({"pair_id": "仅 RECEIVE/ISSUE 允许指定 pair_id"})

    def save(self, *args, **kwargs):
        if self.location_id and not self.warehouse_id:
            self.warehouse_id = self.location.warehouse_id

        # 再次兜底 base_unit
        if self.product_id and getattr(self.product, "base_uom_id", None):
            self.base_unit = self.product.base_uom.code
        self.full_clean()
        return super().save(*args, **kwargs)

# =========================2) 库存调拨（跨仓/跨园区）=========================


# -- 过账日记账：一次具体动作的幂等与审计 --
class PostingJournal(models.Model):
    src_model = models.CharField("来源模型", max_length=32, db_index=True)   # 'WmsTask' / 'WmsTaskLine' ...
    src_id    = models.BigIntegerField("来源ID", db_index=True)
    tx_type   = models.CharField("动作类型", max_length=16, db_index=True)  # 'POST' / 'REVERSE' / 'CANCEL'

    status    = models.CharField("状态", max_length=16, default="PENDING")  # PENDING/POSTED/FAILED
    message   = models.CharField("说明", max_length=255, blank=True, default="")
    attempt_count = models.IntegerField("尝试次数", default=0)
    created_at= models.DateTimeField("创建时间", auto_now_add=True)
    updated_at= models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["src_model","src_id","tx_type"], name="uniq_post_src_tx"
            ),
        ]
        indexes = [
            models.Index(fields=["created_at"], name="idx_post_created"),
            models.Index(fields=["status"], name="idx_post_status"),
        ]

    def __str__(self):
        return f"{self.src_model}#{self.src_id}:{self.tx_type}({self.status})"

from django.db import models

from django.core.exceptions import ValidationError

class ReviewDifference(models.Model):
    """
    复核差异单，记录复核过程中发现的差异
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待复核'
        IN_PROGRESS = 'IN_PROGRESS', '复核中'
        COMPLETED = 'COMPLETED', '已完成'
        CANCELLED = 'CANCELLED', '已取消'

    order_no = models.CharField(max_length=50, unique=True, verbose_name="单号")
    warehouse = models.ForeignKey('locations.Warehouse', on_delete=models.PROTECT, verbose_name="仓库")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    verbose_name="复核人")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name="状态")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    reason = models.TextField(null=True, blank=True, verbose_name="复核原因")
    note = models.TextField(null=True, blank=True, verbose_name="备注")

    def clean(self):
        errors = {}
        if not self.warehouse_id:
            errors["warehouse"] = "必须明确指定复核差异单仓库。"
        if self.status == self.Status.COMPLETED and not self.completed_at:
            errors["completed_at"] = "复核完成后必须填写完成时间。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.warehouse_id:
            raise ValidationError({"warehouse": "必须明确指定复核差异单仓库。"})
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"复核差异单 {self.order_no} - {self.get_status_display()}"

    class Meta:
        verbose_name = "复核差异单"
        verbose_name_plural = "复核差异单"


class ReviewDifferenceLine(models.Model):
    """
    复核差异单明细，记录每项差异的具体信息
    """

    recheck_order = models.ForeignKey(ReviewDifference, on_delete=models.CASCADE, related_name="lines",
                                      verbose_name="复核单")
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT, verbose_name="商品")
    location = models.ForeignKey('locations.Location', on_delete=models.PROTECT, verbose_name="库位")
    batch_no = models.CharField(max_length=50, null=True, blank=True, verbose_name="批次号")
    serial_no = models.CharField(max_length=50, null=True, blank=True, verbose_name="序列号")
    quantity_before = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="复核前数量")
    quantity_after = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="复核后数量")
    quantity_difference = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="差异数量")
    status = models.CharField(max_length=20, choices=ReviewDifference.Status.choices,
                              default=ReviewDifference.Status.PENDING, verbose_name="差异状态")

    def clean(self):
        if self.quantity_after < 0 or self.quantity_difference < 0:
            raise ValidationError("数量差异和复核后数量不能为负数。")

    def __str__(self):
        return f"{self.product} - 差异数量 {self.quantity_difference}"

    class Meta:
        verbose_name = "复核差异单明细"
        verbose_name_plural = "复核差异单明细"

# 用 InventoryDetail 做代理模型，仅用于在 Admin 里挂“库存快调”入口
# 末尾追加（保持你既有 import 和 InventoryDetail 定义不动）
class InventoryQuickInboundAdjust(InventoryDetail):
    class Meta:
        proxy = True
        verbose_name = "入库快调"
        verbose_name_plural = "入库快调"

class InventoryQuickOutboundAdjust(InventoryDetail):
    class Meta:
        proxy = True
        verbose_name = "出库快调"
        verbose_name_plural = "出库快调"
