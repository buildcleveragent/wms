# apps/products/models.py
from __future__ import annotations
from django.core.validators import MaxValueValidator, RegexValidator,MinValueValidator
from django.db.models import Q, F
from decimal import Decimal, ROUND_HALF_UP
from django.db import models
from django.core.exceptions import ValidationError

from allapp.core.models import BaseModel
from allapp.baseinfo.models import Owner

# 可选：严格 GTIN 校验（GS1 Mod10）
# 默认放宽，仅做“纯数字 + 长度(8/12/13/14)”校验。
# 如需启用校验位检测，将此常量置为 True。

ENABLE_GTIN_CHECK_DIGIT = False

def _as_pos_int(x):
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None

def _gtin_mod10_is_valid(num: str) -> bool:
    """
    GS1 Mod10 校验位校验（传入完整含校验位）
    做法：对“去掉校验位”的主体自右向左加权（3,1,3,1...），
    计算得到的校验位应等于末位。
    """
    if not num or not num.isdigit() or len(num) < 2:
        return False
    body, check_digit = num[:-1], int(num[-1])
    total = 0
    for i, ch in enumerate(reversed(body), start=1):
        d = int(ch)
        weight = 3 if i % 2 == 1 else 1  # 右起第1位（不含校验位）权重3
        total += d * weight
    calc = (10 - (total % 10)) % 10
    return calc == check_digit
# =========================
# 字典：分类 / 品牌 / 单位 / 温区
# 全部继承 BaseModel，统一具备 is_active/is_deleted 等
# =========================
class ProductCategory(BaseModel):
    """
    商品分类（自引用形成树形层级）
    """
    code = models.CharField("分类编码", max_length=50, help_text="分类唯一编码")
    name = models.CharField("分类名称", max_length=50, db_index=True)
    parent = models.ForeignKey(
        "self", verbose_name="上级分类",
        null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        verbose_name = "商品分类"
        verbose_name_plural = "商品分类"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_category_code"),
            models.CheckConstraint(check=~Q(code=""), name="chk_category_code_not_empty"),
            # 可选：DB 级防自指（仍需在 clean() 防止“成环”）
            # models.CheckConstraint(check=~Q(id=F("parent")), name="chk_category_no_self_parent"),
            # 可选：同层重名禁止
            # models.UniqueConstraint(fields=["parent", "name"], name="ux_cat_parent_name"),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["parent"]),  # ✅ 用字段名，不是 parent_id
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    # 简易递归（大量数据建议 MPTT/treebeard 或预取）
    def get_all_children(self):
        all_children = []
        for child in self.children.all():
            all_children.append(child)
            all_children.extend(child.get_all_children())
        return all_children

    def get_root_category(self):
        node = self
        # 若频繁调用，建议在调用端 select_related("parent") 以减少查询
        while node.parent_id:
            node = node.parent
        return node

    def clean(self):
        # 规范化
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()

        if not self.code:
            raise ValidationError({"code": "分类编码不能为空"})

        # 应用层防环：不能把 parent 设为自己或子孙
        if self.pk and self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "上级分类不能是自身"})
            # 向上追溯父链，若遇到自己则成环
            p = self.parent
            # 建议在调用端 select_related("parent") 以节省查询
            while p is not None:
                if p.pk == self.pk:
                    raise ValidationError({"parent": "上级分类不能是自身的子孙（会形成环）"})
                p = p.parent

class Brand(BaseModel):
    code = models.CharField("品牌编码", max_length=50, help_text="全局唯一")
    name = models.CharField("品牌名称", max_length=100)
    remark = models.CharField("备注", max_length=255, blank=True)  # 如需 NULL -> null=True

    class Meta:
        verbose_name = "品牌"
        verbose_name_plural = "品牌"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_brand_code"),
            models.CheckConstraint(check=~Q(code=""), name="chk_brand_code_not_empty"),
        ]
        indexes = [
            models.Index(fields=["is_active", "code"], name="idx_brand_active_code"),
            # 可按需：models.Index(fields=["name"], name="idx_brand_name"),
        ]

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()
        if not self.code:
            raise ValidationError({"code": "品牌编码不能为空"})

    def __str__(self):
        return f"{self.code} - {self.name}"

class ProductUom(BaseModel):
    """全局计量单位字典（概念层 + 元数据）"""
    code = models.CharField(
        "单位编码", max_length=20,
        help_text="EA/PCS/CTN/PLT/KG/L 等",
        validators=[RegexValidator(r"^[A-Za-z0-9_\-\*]+$", "仅允许字母、数字、下划线、连字符、星号")]
    )
    name = models.CharField("单位名称", max_length=50)

    class Kind(models.TextChoices):
        COUNT  = "COUNT",  "计数"
        WEIGHT = "WEIGHT", "重量"
        VOLUME = "VOLUME", "体积"
        LENGTH = "LENGTH", "长度"
        AREA   = "AREA",   "面积"
        OTHER  = "OTHER",  "其他"

    kind = models.CharField("类型", max_length=12, choices=Kind.choices, default=Kind.COUNT)

    # 小数位数：建议用 SmallInteger + 上界 6（按你系统的统一精度）
    decimal_places = models.PositiveSmallIntegerField("小数位数", default=0,
                                                      validators=[MaxValueValidator(6)])

    class Meta:
        verbose_name = "计量单位"
        verbose_name_plural = "计量单位"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_uom_code"),
            models.CheckConstraint(check=~Q(code=""), name="chk_uom_code_not_empty"),
            # 双保险（如果不想用 MaxValueValidator，可以改用 DB 约束）：
            # models.CheckConstraint(
            #     check=Q(decimal_places__gte=0) & Q(decimal_places__lte=6),
            #     name="chk_uom_dp_0_6"
            # )
        ]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        # 用中文标签展示 kind，更友好
        return f"{self.name}"

    def clean(self):
        super().clean()
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()
        if not self.code:
            raise ValidationError({"code": "单位编码不能为空"})

    def save(self, *args, **kwargs):
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()
        super().save(*args, **kwargs)

class TemperatureZone(BaseModel):
    """温区字典（适用于冷链/医药）"""

    class StorageCondition(models.TextChoices):
        AMBIENT      = "AMBIENT",      "常温"
        REFRIGERATED = "REFRIGERATED", "冷藏"
        FROZEN       = "FROZEN",       "冷冻"

    code = models.CharField("温区代码", max_length=20, help_text="温区代码")
    name = models.CharField("温区名称", max_length=50)

    # 允许负温度；加字段级范围校验防止离谱数据（按需调整上下限）
    min_temp = models.DecimalField(
        "最低温度(°C)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("-100.00")), MaxValueValidator(Decimal("100.00"))]
    )
    max_temp = models.DecimalField(
        "最高温度(°C)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(Decimal("-100.00")), MaxValueValidator(Decimal("100.00"))]
    )

    storage_condition = models.CharField(
        "存储条件", max_length=20, blank=True, choices=StorageCondition.choices,
    )

    class Meta:
        verbose_name = "温区"
        verbose_name_plural = "温区"
        ordering = ["min_temp"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_tempzone_code"),
            models.CheckConstraint(check=Q(min_temp__lte=F("max_temp")), name="chk_tempzone_min_le_max"),
            # 可选：DB 端再兜一层合理范围（和字段 validators 二选一/都保留都行）
            # models.CheckConstraint(
            #     check=Q(min_temp__gte=Decimal("-100.00")) & Q(max_temp__lte=Decimal("100.00")),
            #     name="chk_tempzone_range",
            # ),
            models.CheckConstraint(check=~Q(code=""), name="chk_tempzone_code_not_empty"),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
            # 可选：按区间检索时可能有用
            models.Index(fields=["min_temp", "max_temp"], name="idx_tempzone_range"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name} ({self.min_temp}~{self.max_temp}°C)"

    def clean(self):
        super().clean()
        # 规范化
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()
        if not self.code:
            raise ValidationError({"code": "温区代码不能为空"})

        # 业务一致性（可选）：若选择了存储条件，要求温度区间与常识匹配
        if self.storage_condition:
            lo = self.min_temp
            hi = self.max_temp
            sc = self.storage_condition
            # 这些阈值可按你们 SOP 调整
            if sc == self.StorageCondition.AMBIENT and not (Decimal("10.00") <= lo and hi <= Decimal("30.00")):
                raise ValidationError({"storage_condition": "常温建议区间约 10~30℃（请按内部标准调整）"})
            if sc == self.StorageCondition.REFRIGERATED and not (Decimal("0.00") <= lo and hi <= Decimal("8.00")):
                raise ValidationError({"storage_condition": "冷藏建议区间约 0~8℃（请按内部标准调整）"})
            if sc == self.StorageCondition.FROZEN and not (Decimal("-30.00") <= lo and hi <= Decimal("0.00")):
                raise ValidationError({"storage_condition": "冷冻建议区间约 -30~0℃（请按内部标准调整）"})

# =========================主表：Product =========================
class Product(BaseModel):
    PACK_REQ_CHOICES = [
        ("NONE", "无（不需要打包）"),
        ("BAG", "袋装/气泡袋"),
        ("BOX", "装箱"),
        ("SHRINK", "缠绕/热缩"),
        ("PALLET", "打托/缠膜"),
    ]

    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name="products", verbose_name="货主")

    # 基本信息
    code = models.CharField("商品编号", max_length=50, help_text="货主内唯一")
    name = models.CharField("商品名称", max_length=200)
    spec = models.CharField("规格", max_length=200, blank=True, null=True)
    sku = models.CharField("SKU编码", max_length=50, help_text="货主内（有值时）唯一")
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, null=True, blank=True, verbose_name="分类", related_name="products")
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, null=True, blank=True, verbose_name="品牌", related_name="products")
    temperature_zone = models.ForeignKey(TemperatureZone, on_delete=models.PROTECT, null=True, blank=True, verbose_name="温度区域",
                              related_name="products")
    description = models.TextField("描述", blank=True, null=True)

    # 条码（更细可在 ProductPackage 中维护层级条码）
    gtin = models.CharField("标准条码(GTIN/EAN/UPC)", max_length=20, blank=True, null=True)
    unit_barcode = models.CharField("零码", max_length=50, blank=True, null=True)
    carton_barcode = models.CharField("箱码", max_length=50, blank=True, null=True)

    pack_requirement = models.CharField(
        "打包要求", max_length=20, choices=PACK_REQ_CHOICES, default="NONE", db_index=True
    )
    # （可选）补充自由备注，不想要就省略
    pack_note = models.CharField("打包备注", max_length=120, blank=True, default="")

    # 单位
    base_uom = models.ForeignKey(
        ProductUom, on_delete=models.PROTECT, related_name="as_base_of_products", verbose_name="基本单位"
    )

    # 拣配&补货策略
    class PickPolicy(models.TextChoices):
        AUTO = "AUTO", "优先整箱可用则整箱，否则散件"
        BASE_ONLY = "BASE_ONLY", "只允许散件"
        AUX_ONLY = "AUX_ONLY", "只允许整箱(不破箱)"
        OPTIMIZE = "OPTIMIZE", "按效率优化：整箱优先，尾差阈值内散拣"

    pick_policy = models.CharField("拣配策略", max_length=12, choices=PickPolicy.choices, default=PickPolicy.AUTO )
    break_box_allowed = models.BooleanField("允许破箱", default=True)
    min_pick_multiple = models.PositiveIntegerField("最小拣配倍数(基本单位)", default=1)
    replenish_min = models.PositiveIntegerField("前置区补货下限(基本单位)", default=0)
    replenish_uom = models.ForeignKey(ProductUom, on_delete=models.PROTECT, null=True, blank=True, verbose_name="补货单位")

    # 体积&重量（基本单位）
    volume = models.DecimalField("体积(基本单位,m³)", max_digits=12, decimal_places=6, blank=True, null=True)
    weight = models.DecimalField("重量(基本单位,kg)", max_digits=12, decimal_places=3, blank=True, null=True)
    net_content =models.DecimalField("净含量(基本单位,g)", max_digits=12, decimal_places=3, blank=True, null=True)

    # 库存阈值（NULL 表示不设）
    min_stock = models.DecimalField("最低库存(基本单位)", max_digits=12, decimal_places=2, blank=True, null=True)
    max_stock = models.DecimalField("最高库存(基本单位)", max_digits=12, decimal_places=2, blank=True, null=True)

    # 批次 / 序列号 / 效期
    serial_control = models.BooleanField("序列号管理", default=False)
    batch_control = models.BooleanField("批次管理", default=True)
    expiry_control = models.BooleanField("保质期管理", default=True)

    product_image = models.ImageField("商品图片",
        upload_to='products/',  # 按日期分目录存储
        blank=True,
        null=True
    )

    # 定价相关
    pricing_strategy = models.CharField("定价策略",
        max_length=20,
        choices=[('WAC', '加权平均法'), ('NEW', '按最新批次')],
        default='WAC'  # 默认使用加权平均法
    )  # 定价策略字段
    price = models.DecimalField("价格",max_digits=10, decimal_places=2)  # 基本价格字段
    min_price = models.DecimalField("最低价格",max_digits=10, decimal_places=2, blank=True, null=True)
    max_discount = models.DecimalField("最高折扣%", max_digits=10, decimal_places=2, blank=True, null=True)

    class ExpiryBasis(models.TextChoices):
        MFG = "MFG", "生产日期"
        INBOUND = "INBOUND", "入库日期"
    expiry_basis = models.CharField("效期基准", max_length=10, choices=ExpiryBasis.choices, blank=True, null=True,default="MFG" )

    shelf_life_days = models.PositiveIntegerField("保质期天数", blank=True, null=True)
    inbound_valid_days = models.PositiveIntegerField("入库有效天数(入库基准)", blank=True, null=True)
    expiry_warning_days = models.PositiveIntegerField("效期预警阈值(剩余天数)", blank=True, null=True)
    fefo_required = models.BooleanField("FEFO拣选(先到期先出)", default=True)
    mix_lot_allowed = models.BooleanField("允许库位混批", default=False)
    mix_expiry_allowed = models.BooleanField("允许库位混效期", default=False)

    # 温控（如采用多温区模型，可不使用下两字段）
    # temperature_min = models.DecimalField("最低温(°C)", max_digits=5, decimal_places=2, blank=True, null=True)
    # temperature_max = models.DecimalField("最高温(°C)", max_digits=5, decimal_places=2, blank=True, null=True)

    # 产地（ISO-3166-1 alpha-2）
    origin_country = models.CharField(
        "原产国(ISO-2)", max_length=2, blank=True, null=True,
        validators=[RegexValidator(r'^[A-Z]{2}$', "必须为两位大写字母的 ISO-2 代码")]
    )

    # 成本
    # class CostMethod(models.TextChoices):
    #     FIFO = "FIFO", "先进先出"
    #     MA = "MA", "移动加权平均"
    #
    # cost_method = models.CharField(
    #     "成本方法", max_length=10, choices=CostMethod.choices, default=CostMethod.MA
    # )
    # standard_cost = models.DecimalField("标准成本(基本单位)", max_digits=12, decimal_places=4, default=0)

    external_code = models.CharField("外部系统编码", max_length=50, blank=True, null=True)
    extra = models.JSONField("扩展属性", blank=True, null=False, default=dict)  # 建议默认空 dict
    material_quality = models.CharField("材质", max_length=20, blank=True, null=True)
    vender = models.CharField("厂家", max_length=50, blank=True, null=True)

    class Meta:
        verbose_name = "商品档案"
        verbose_name_plural = "商品档案"
        ordering = ["owner", "code"]
        constraints = [
            models.CheckConstraint(check=~Q(code=""), name="chk_prod_code_not_empty"),
            models.UniqueConstraint(fields=["owner", "code"], name="uniq_owner_code"),
            models.UniqueConstraint(fields=["owner", "sku"], name="uniq_owner_sku"),
            models.UniqueConstraint(fields=["owner", "gtin"], name="uniq_owner_gtin"),
            models.UniqueConstraint(fields=["owner", "unit_barcode"], name="uniq_owner_unit_barcode"),
            models.UniqueConstraint(fields=["owner", "carton_barcode"], name="uniq_owner_carton_barcode"),
            models.UniqueConstraint(fields=["owner", "external_code"], name="uniq_owner_external_code"),

            # 关键业务约束（MySQL 8 支持 CHECK）
            models.CheckConstraint(check=Q(min_pick_multiple__gte=1), name="chk_min_pick_multiple_ge_1"),
            # models.CheckConstraint(
            #     check=(Q(temperature_min__isnull=True) | Q(temperature_max__isnull=True) |
            #            Q(temperature_min__lte=models.F("temperature_max"))),
            #     name="chk_temp_min_le_max_or_null",
            # ),
            models.CheckConstraint(
                check=(Q(min_stock__isnull=True) | Q(max_stock__isnull=True) | Q(min_stock__lt=models.F("max_stock"))),
                name="chk_min_stock_lt_max",
            ),

            models.CheckConstraint(
                check=(~Q(pick_policy="AUX_ONLY") | Q(break_box_allowed=False)),
                name="chk_aux_only_no_break",
            ),
            models.CheckConstraint(
                check=(Q(expiry_control=False) | Q(expiry_basis__isnull=False)),
                name="chk_expiry_requires_basis",
            ),
            models.CheckConstraint(
                check=(
                        Q(expiry_control=False) |
                        (Q(expiry_basis="MFG") & Q(shelf_life_days__gt=0)) |
                        (Q(expiry_basis="INBOUND") & Q(inbound_valid_days__gt=0))
                ),
                name="chk_expiry_days_valid",
            ),

            models.CheckConstraint(
                name="chk_expiry_warning_bounds",
                check=(
                        Q(expiry_control=False)
                        | Q(expiry_warning_days__isnull=True)
                        | (
                                (Q(expiry_basis="MFG")
                                 & Q(expiry_warning_days__gt=0)
                                 & Q(expiry_warning_days__lt=models.F("shelf_life_days")))
                                |
                                (Q(expiry_basis="INBOUND")
                                 & Q(expiry_warning_days__gt=0)
                                 & Q(expiry_warning_days__lt=models.F("inbound_valid_days")))
                        )
                ),
            ),

        ]

        # 高价值查询索引（覆盖典型过滤/联表）
        indexes = [
            models.Index(fields=["owner", "is_active"]),

            models.Index(fields=["temperature_zone"]),
            models.Index(fields=["owner", "name"], name="owner_name_prefix_idx"),  # LIKE 'xxx%' 前缀有效

            models.Index(fields=["category"]),
            models.Index(fields=["brand"]),
            models.Index(fields=["owner", "category", "is_active"], name="owner_category_active"),
            models.Index(fields=["owner", "brand", "is_active"], name="owner_brand_active"),
            models.Index(fields=["owner", "is_active", "batch_control"], name="owner_active_batch_idx"),
            models.Index(fields=["owner", "is_active", "expiry_control"], name="owner_active_expiry_idx"),
            # models.Index(fields=["owner", "is_active", "is_hazardous"], name="owner_active_hazard_idx"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    # 便捷：首选拣配包装
    # @property
    # def primary_pick_package(self) -> Optional["ProductPackage"]:
    #     return self.packages.filter(is_pickable=True).order_by("sort_order", "uom__code").first()
    @property
    def primary_pick_package(self):
        return (
            self.packages.select_related("uom")
            .only("id", "product_id", "uom_id", "sort_order")
            .order_by("sort_order", "uom__code")
            .first()
        )

    def clean(self):
        super().clean()
        errors = {}
        # 统一大小写 & 去两端空白
        if self.code:
            self.code = self.code.strip().upper()

        if not self.code:
            errors["code"] = "商品编号不能为空"

        if self.sku:
            self.sku = self.sku.strip().upper()

        for f in ["gtin", "unit_barcode", "carton_barcode", "external_code"]:
            v = getattr(self, f, None)
            if isinstance(v, str):
                setattr(self, f, v.strip() or None)

        if self.origin_country:
            self.origin_country = self.origin_country.upper()

        self.shelf_life_days = _as_pos_int(self.shelf_life_days)
        self.inbound_valid_days = _as_pos_int(self.inbound_valid_days)
        self.expiry_warning_days = _as_pos_int(self.expiry_warning_days)

        # GTIN（默认宽松；如启用校验位则做 Mod10 校验）
        if self.gtin:
            if not self.gtin.isdigit() or len(self.gtin) not in (8, 12, 13, 14):
                errors["gtin"] = "GTIN必须为8/12/13/14位数字"
            elif ENABLE_GTIN_CHECK_DIGIT and not _gtin_mod10_is_valid(self.gtin):
                errors["gtin"] = "GTIN 校验位不通过（GS1 Mod10）"

        # 库存上下限
        if self.min_stock is not None and self.max_stock is not None and self.min_stock >= self.max_stock:
            errors["min_stock"] = "最低库存必须小于最高库存（或将最高库存留空表示不设上限）。"

        # 基本单位类型
        # if self.base_uom and self.base_uom.kind not in ("COUNT", "WEIGHT", "VOLUME"):
        #     errors["base_uom"] = f"基本单位类型必须为计数/重量/体积，当前类型：{self.base_uom.get_kind_display()}"

        # 效期 & FEFO
        if self.expiry_control:
            if not self.expiry_basis:
                errors["expiry_basis"] = "启用保质期管理时必须选择效期基准。"
            if self.expiry_basis == "MFG":
                if not self.shelf_life_days or self.shelf_life_days <= 0:
                    errors["shelf_life_days"] = "按生产日期管理时，保质期天数必须 > 0。"
                if self.expiry_warning_days is not None:
                    if self.expiry_warning_days <= 0 or self.expiry_warning_days >= self.shelf_life_days:
                        errors["expiry_warning_days"] = "预警天数必须在 1 ~ 保质期天数-1 之间。"
            if self.expiry_basis == "INBOUND":
                if not self.inbound_valid_days or self.inbound_valid_days <= 0:
                    errors["inbound_valid_days"] = "按入库日期管理时，入库有效天数必须 > 0。"
                if self.expiry_warning_days is not None:
                    if self.expiry_warning_days <= 0 or self.expiry_warning_days >= self.inbound_valid_days:
                        errors["expiry_warning_days"] = "预警天数必须在 1 ~ 入库有效天数-1 之间。"
        # else:
        #     # 关闭效期时，统一清理相关字段，避免脏数据
        #     self.expiry_basis = None
        #     self.shelf_life_days = None
        #     self.inbound_valid_days = None
        #     self.expiry_warning_days = None
        #     self.fefo_required = False

        # 温度范围
        # if self.temperature_min is not None and self.temperature_max is not None and self.temperature_min > self.temperature_max:
        #     errors["temperature_min"] = "最低温不能高于最高温。"

        # 序列号商品不建议混批
        # if self.serialno_control and self.mix_lot_allowed:
        #     errors["mix_lot_allowed"] = "序列号管理商品不建议库位混批。"

        # 拣配策略边界
        if self.pick_policy == "AUX_ONLY" and self.break_box_allowed:
            errors["break_box_allowed"] = "整箱策略(AUX_ONLY)下不应允许破箱。"

        if self.min_pick_multiple and self.min_pick_multiple < 1:
            errors["min_pick_multiple"] = "最小拣配倍数必须 ≥ 1。"

        # 补货参数一致性
        if (self.replenish_min or 0) > 0 and not self.replenish_uom_id:
            errors["replenish_uom"] = "设置补货下限时必须指定补货单位。"
        if self.replenish_uom_id:
            # if self.replenish_uom.kind != "COUNT":
            #     errors["replenish_uom"] = "补货单位必须为计数型(如箱/托)。"
            # 更新场景且已有包装层级时，补货单位必须存在于包装层级中
            if self.pk and self.packages.exists():
                if not self.packages.filter(uom_id=self.replenish_uom_id).exists():
                    errors["replenish_uom"] = "补货单位必须存在于该商品的包装层级中。"

        # ========= 唯一性校验（包含软删除记录）=========
        # 说明：默认 manager 往往会过滤软删数据，导致表单校验通过、最终落库时触发 DB IntegrityError(500)
        # 用 all_objects（若存在）把软删也纳入检查，提前给出友好提示。
        mgr = getattr(type(self), "all_objects", None) or type(self)._base_manager

        def _uniq_owner_field(field: str, label: str):
            val = getattr(self, field, None)
            if val in (None, "") or not self.owner_id:
                return
            qs = mgr.filter(owner_id=self.owner_id, **{field: val})
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            conflict = qs.only("id", "code", "name", "is_deleted").first()
            if conflict:
                if getattr(conflict, "is_deleted", False):
                    errors[field] = (
                        f"该货主下{label}“{val}”已存在（已软删除：{conflict.code}-{conflict.name}）。"
                        f"请先恢复旧商品，或使用新的{label}。"
                    )
                else:
                    errors[field] = f"该货主下{label}“{val}”已存在（{conflict.code}-{conflict.name}）。"

        _uniq_owner_field("code", "商品编号")
        _uniq_owner_field("sku", "SKU编码")
        _uniq_owner_field("gtin", "标准条码")
        _uniq_owner_field("unit_barcode", "零码")
        _uniq_owner_field("carton_barcode", "箱码")
        _uniq_owner_field("external_code", "外部系统编码")


        if self.pk:
            orig = type(self).objects.only(
                "code", "sku", "gtin", "unit_barcode", "carton_barcode", "external_code"
            ).get(pk=self.pk)

            def _norm(x):
                if isinstance(x, str):
                    x = x.strip()
                    return x or None
                return x

            changed = []
            for f in ["code", "sku", "gtin", "unit_barcode", "carton_barcode", "external_code"]:
                if _norm(getattr(self, f)) != _norm(getattr(orig, f)):
                    changed.append(f)

            if changed:
                errors["code"] = (
                    f"禁止在原记录上修改标识字段：{', '.join(changed)}；"
                    f"如需变更请新建一条并停用/软删旧记录。"
                )

        if errors:
            raise ValidationError(errors)

# ========================= 商品 × 包装层级# =========================
class ProductPackage(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="packages", verbose_name="商品",related_query_name="product_package")
    uom = models.ForeignKey(ProductUom, on_delete=models.PROTECT, related_name="packages", verbose_name="包装单位")
    qty_in_base = models.PositiveIntegerField("换算数量")

    barcode = models.CharField("层级条码", max_length=50, blank=True, null=True)
    length_cm = models.DecimalField("长(cm)", max_digits=8, decimal_places=2, blank=True, null=True)
    width_cm  = models.DecimalField("宽(cm)", max_digits=8, decimal_places=2, blank=True, null=True)
    height_cm = models.DecimalField("高(cm)", max_digits=8, decimal_places=2, blank=True, null=True)
    gross_weight_kg = models.DecimalField("毛重(kg)", max_digits=10, decimal_places=3, blank=True, null=True)
    volume_m3 = models.DecimalField("体积(m³)", max_digits=12, decimal_places=6, blank=True, null=True)
    volume_auto = models.BooleanField("体积自动计算", default=True)

    class VolumeStatus(models.TextChoices):
        NONE = "", "未校验"
        CALCULATED = "CALCULATED", "已计算"
        MISMATCH = "MISMATCH", "手输与计算不一致"

    volume_m3_status = models.CharField(
        "体积状态", max_length=12, choices=VolumeStatus.choices, default=VolumeStatus.NONE, blank=True
    )

    is_pickable = models.BooleanField("可直接拣配", default=False)
    is_purchase_default = models.BooleanField("采购辅助单位", null=True, blank=True, default=None)
    is_sales_default = models.BooleanField("销售辅助单位", null=True, blank=True, default=None)

    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        verbose_name = "商品包装层级"
        verbose_name_plural = "商品包装层级"
        ordering = ["product", "sort_order", "uom"]   # ✅ 用字段名
        constraints = [
            models.UniqueConstraint(fields=["product", "uom"], name="uniq_product_uom"),
            # models.UniqueConstraint(fields=["product", "is_purchase_default"], name="uniproduct_purchasedefault"),
            # models.UniqueConstraint(fields=["product", "is_sales_default"], name="uniproduct_salesdefault"),
            # ✅ 每商品最多 1 条“采购默认”(True)（非默认用 NULL 不参与冲突）
            models.UniqueConstraint(
                fields=["product", "is_purchase_default", "is_deleted"],
                name="uniq_prod_purchase_default_true",
            ),

            # ✅ 每商品最多 1 条“销售默认”(True)
            models.UniqueConstraint(
                fields=["product", "is_sales_default", "is_deleted"],
                name="uniq_prod_sales_default_true",
            ),
            models.CheckConstraint(check=Q(qty_in_base__gt=0), name="chk_qty_in_base_gt_0"),
            # 尺寸三者要么都为空，要么都 >0（可按需保留）
            models.CheckConstraint(
                name="chk_dims_all_or_none",
                check=(
                    (Q(length_cm__isnull=True) & Q(width_cm__isnull=True) & Q(height_cm__isnull=True)) |
                    (Q(length_cm__gt=0) & Q(width_cm__gt=0) & Q(height_cm__gt=0))
                ),
            ),
            # 体积、毛重非负（NULL 允许）
            models.CheckConstraint(
                name="chk_nonneg_weight_volume",
                check=(Q(gross_weight_kg__isnull=True) | Q(gross_weight_kg__gte=0)) &
                      (Q(volume_m3__isnull=True) | Q(volume_m3__gte=0)),
            ),
            # 可选：同一商品同条码唯一（允许多个 NULL）
            models.UniqueConstraint(fields=["product", "barcode"], name="uni_pkg_pro_barcode"),
        ]
        indexes = [
            models.Index(fields=["product", "uom"]),                    # ✅
            models.Index(fields=["product", "sort_order"], name="idx_pkg_prod_sort"),  # ✅ 去重
            models.Index(fields=["barcode"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        # 注意：大量列表渲染时请 select_related("product__base_uom", "uom")
        return f"{self.product.code} - 1 {self.uom.code} = {self.qty_in_base} {self.product.base_uom.code}"

    def save(self, *args, **kwargs):
        # ✅ 只做“自动计算/赋值”，不要在 save() 里 full_clean()（否则 admin 容易 500）
        if self.volume_auto:
            if (self.length_cm and self.length_cm > 0) and \
                    (self.width_cm and self.width_cm > 0) and \
                    (self.height_cm and self.height_cm > 0):
                calc = (self.length_cm * self.width_cm * self.height_cm) / Decimal("1000000")
                calc_q = calc.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                if self.volume_m3 is None:
                    self.volume_m3 = calc_q
                    self.volume_m3_status = self.VolumeStatus.CALCULATED
                else:
                    tol = max(Decimal("0.000001"), calc_q * Decimal("0.001"))  # 容差 max(1e-6, 0.1%)
                    if (self.volume_m3 - calc_q).copy_abs() > tol:
                        self.volume_m3_status = self.VolumeStatus.MISMATCH
                    else:
                        self.volume_m3 = calc_q
                        self.volume_m3_status = self.VolumeStatus.CALCULATED
            else:
                self.volume_m3_status = self.VolumeStatus.NONE
                self.volume_m3 = None

        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}

        # --- 规范化 ---
        if isinstance(self.barcode, str):
            self.barcode = self.barcode.strip() or None

        # --- 基础校验 ---
        if not self.uom_id:
            errors["uom"] = "请选择包装单位。"
        if (self.qty_in_base or 0) <= 0:
            errors["qty_in_base"] = "换算数量必须 > 0。"

        # 与基本单位相同则必须 1:1
        if self.uom_id and self.product_id and self.uom_id == self.product.base_uom_id and self.qty_in_base != 1:
            errors["qty_in_base"] = "基础单位层级的换算数必须为 1。"

        # --- ✅ 关键：把“会导致 500 的唯一性错误”提前到 clean()，让 admin 当作表单错误显示 ---
        if self.product_id and self.uom_id:
            qs = type(self).objects.filter(product_id=self.product_id, uom_id=self.uom_id, is_deleted=False)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                # 放在 __all__，效果与 Django 默认“字段组合已存在”类似
                errors["__all__"] = "包含 商品 和 包装单位 的 商品包装层级 已经存在。"

        if self.product_id and self.barcode:
            qs = type(self).objects.filter(product_id=self.product_id, barcode=self.barcode, is_deleted=False)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors["barcode"] = "该商品下此层级条码已存在。"

        # 每商品的“默认单位”唯一（应用层校验；并发场景仍由 DB 约束兜底）
        for flag, label in (("is_purchase_default", "采购"), ("is_sales_default", "销售")):
            if getattr(self, flag):
                qs = type(self).objects.filter(product_id=self.product_id, **{flag: True}, is_deleted=False)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                if qs.exists():
                    conflict = qs.first()
                    errors[flag] = f"该商品已有默认{label}单位：{conflict.uom.code}"

        if self.barcode and not (3 <= len(self.barcode) <= 50):
            errors["barcode"] = "条码长度需在 3~50 之间。"

        if errors:
            raise ValidationError(errors)

    # def save(self, *args, **kwargs):
    #     # 自动计算体积（cm→m³），三维都>0才计算
    #     if self.volume_auto:
    #         if (self.length_cm and self.length_cm > 0) and \
    #            (self.width_cm  and self.width_cm  > 0) and \
    #            (self.height_cm and self.height_cm > 0):
    #             calc = (self.length_cm * self.width_cm * self.height_cm) / Decimal("1000000")
    #             calc_q = calc.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    #             if self.volume_m3 is None:
    #                 self.volume_m3 = calc_q
    #                 self.volume_m3_status = self.VolumeStatus.CALCULATED
    #             else:
    #                 tol = max(Decimal("0.000001"), calc_q * Decimal("0.001"))  # 容差 max(1e-6, 0.1%)
    #                 if (self.volume_m3 - calc_q).copy_abs() > tol:
    #                     self.volume_m3_status = self.VolumeStatus.MISMATCH
    #                 else:
    #                     self.volume_m3 = calc_q
    #                     self.volume_m3_status = self.VolumeStatus.CALCULATED
    #         else:
    #             self.volume_m3_status = self.VolumeStatus.NONE
    #             self.volume_m3 = None
    #
    #     # 严格校验
    #     self.full_clean()
    #     return super().save(*args, **kwargs)
    #
    # def clean(self):
    #     errors = {}
    #
    #     if not self.uom_id:
    #         errors["uom"] = "请选择包装单位。"
    #     if (self.qty_in_base or 0) <= 0:
    #         errors["qty_in_base"] = "换算数量必须 > 0。"
    #
    #     # 与基本单位相同则必须 1:1
    #     if self.uom_id and self.product_id and self.uom_id == self.product.base_uom_id and self.qty_in_base != 1:
    #         errors["qty_in_base"] = "基础单位层级的换算数必须为 1。"
    #
    #     # 每商品的“默认单位”唯一（应用层校验；并发场景请在服务层加锁）
    #     for flag, label in (("is_purchase_default", "采购"), ("is_sales_default", "销售")):
    #         if getattr(self, flag):
    #             qs = type(self).objects.filter(product_id=self.product_id, **{flag: True}, is_deleted=False)
    #             if self.pk:
    #                 qs = qs.exclude(pk=self.pk)
    #             if qs.exists():
    #                 conflict = qs.first()
    #                 errors[flag] = f"该商品已有默认{label}单位：{conflict.uom.code}"
    #
    #     # 条码长度基本校验
    #     if self.barcode and not (3 <= len(self.barcode) <= 50):
    #         errors["barcode"] = "条码长度需在 3~50 之间。"
    #
    #     if errors:
    #         raise ValidationError(errors)

