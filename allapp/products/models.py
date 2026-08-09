# apps/products/models.py
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.core.models import BaseModel, SoftDeleteManager

# 可选：严格 GTIN 校验（GS1 Mod10）
# 默认放宽，仅做“纯数字 + 长度(8/12/13/14)”校验。
# 如需启用校验位检测，将此常量置为 True。

ENABLE_GTIN_CHECK_DIGIT = False

PRODUCT_IDENTIFIER_FIELDS = (
    "code",
    "sku",
    "gtin",
    "unit_barcode",
    "carton_barcode",
    "external_code",
)
STABLE_PRODUCT_IDENTIFIER_FIELDS = ("owner", "code", "sku")
LEGACY_PRODUCT_IDENTIFIER_FIELDS = (
    "gtin",
    "unit_barcode",
    "carton_barcode",
    "external_code",
)


def normalize_product_identifier(value) -> str:
    """Return the canonical comparison form used by the identifier registry."""
    return str(value).strip().upper() if value not in (None, "") else ""


class ProductQuerySet(models.QuerySet):
    """Prevent writes that would bypass Product.save() and its registry sync."""

    def update(self, **kwargs):
        blocked = set(kwargs).intersection(
            (*PRODUCT_IDENTIFIER_FIELDS, "carton_package", "carton_package_id")
        )
        if blocked:
            fields = ", ".join(sorted(blocked))
            raise ValueError(f"商品标识字段不能通过 QuerySet.update() 修改：{fields}")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        blocked = set(fields).intersection(
            (*PRODUCT_IDENTIFIER_FIELDS, "carton_package", "carton_package_id")
        )
        if blocked:
            names = ", ".join(sorted(blocked))
            raise ValueError(f"商品标识字段不能通过 bulk_update() 修改：{names}")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, *args, **kwargs):
        raise ValueError(
            "Product 不支持 bulk_create()；请逐个调用 save() 以同步商品标识注册表。"
        )

    def delete(self):
        raise ValidationError("商品包含永久标识占用，不允许硬删除；请使用软删除。")


class ProductManager(SoftDeleteManager.from_queryset(ProductQuerySet)):
    pass


class AllProductManager(models.Manager.from_queryset(ProductQuerySet)):
    pass


class ImmutableIdentifierQuerySet(models.QuerySet):
    immutable_fields = set()

    def update(self, **kwargs):
        blocked = set(kwargs).intersection(self.immutable_fields)
        if blocked:
            raise ValueError(
                "标识身份字段不能通过 QuerySet.update() 修改："
                + ", ".join(sorted(blocked))
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        blocked = set(fields).intersection(self.immutable_fields)
        if blocked:
            raise ValueError(
                "标识身份字段不能通过 bulk_update() 修改：" + ", ".join(sorted(blocked))
            )
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, *args, **kwargs):
        raise ValueError("标识记录不支持 bulk_create()；请使用统一标识服务。")

    def delete(self):
        raise ValidationError("标识历史不得硬删除；请执行退役操作。")


class ProductBarcodeQuerySet(ImmutableIdentifierQuerySet):
    immutable_fields = {
        "owner",
        "owner_id",
        "product",
        "product_id",
        "barcode",
        "normalized_value",
        "barcode_type",
        "package",
        "package_id",
        "qty_in_base",
        "primary_scope",
    }


class ProductExternalIdentifierQuerySet(ImmutableIdentifierQuerySet):
    immutable_fields = {
        "owner",
        "owner_id",
        "product",
        "product_id",
        "source_system",
        "external_code",
        "normalized_value",
        "primary_scope",
    }


class ProductPackageQuerySet(models.QuerySet):
    """Prevent writes that would bypass package barcode registry sync."""

    def update(self, **kwargs):
        blocked = set(kwargs).intersection({"barcode", "product", "product_id"})
        if blocked:
            names = ", ".join(sorted(blocked))
            raise ValueError(f"包装关键字段不能通过 QuerySet.update() 修改：{names}")
        if (kwargs.get("is_deleted") is True or kwargs.get("is_active") is False) and (
            Product.all_objects.filter(carton_package_id__in=self.values("pk")).exists()
        ):
            raise ValidationError("包含已绑定商品箱码的包装层级，不能停用或删除。")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        blocked = set(fields).intersection({"barcode", "product", "product_id"})
        if blocked:
            names = ", ".join(sorted(blocked))
            raise ValueError(f"包装关键字段不能通过 bulk_update() 修改：{names}")
        if set(fields).intersection({"is_active", "is_deleted"}):
            bound_ids = set(
                Product.all_objects.filter(
                    carton_package_id__in=[obj.pk for obj in objs if obj.pk]
                ).values_list("carton_package_id", flat=True)
            )
            if any(
                obj.pk in bound_ids and (obj.is_deleted or not obj.is_active)
                for obj in objs
            ):
                raise ValidationError("包含已绑定商品箱码的包装层级，不能停用或删除。")
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, *args, **kwargs):
        raise ValueError(
            "ProductPackage 不支持 bulk_create()；请逐个调用 save() 以同步商品标识注册表。"
        )


class ProductPackageManager(SoftDeleteManager.from_queryset(ProductPackageQuerySet)):
    pass


class AllProductPackageManager(models.Manager.from_queryset(ProductPackageQuerySet)):
    pass


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
    商品分类（自引用形成最多三级的商城分类树）。
    """

    MAX_DEPTH = 3
    LEVEL_NAMES = {1: "大类", 2: "中类", 3: "小类"}

    code = models.CharField("分类编码", max_length=50, help_text="分类唯一编码")
    name = models.CharField("分类名称", max_length=50, db_index=True)
    parent = models.ForeignKey(
        "self",
        verbose_name="上级分类",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    sort_order = models.PositiveIntegerField("商城排序", default=0, db_index=True)
    image = models.ImageField(
        "分类图片",
        upload_to="product_categories/",
        null=True,
        blank=True,
        help_text="主要用于商城顶部大类图标；未上传时显示分类名称首字。",
    )

    class Meta:
        verbose_name = "商品分类"
        verbose_name_plural = "商品分类"
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_category_code"),
            models.CheckConstraint(
                check=~Q(code=""), name="chk_category_code_not_empty"
            ),
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

    def save(self, *args, **kwargs):
        # Category writes are infrequent; enforcing the tree rules here also covers
        # scripts and APIs that do not use a ModelForm.
        self.full_clean()
        return super().save(*args, **kwargs)

    def ancestor_chain(self, *, include_self=True):
        """Return the root-to-node chain and remain safe against dirty cyclic data."""
        chain = []
        seen = set()
        node = self if include_self else self.parent
        while node is not None and node.pk not in seen:
            if node.pk is not None:
                seen.add(node.pk)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    @property
    def depth(self):
        return len(self.ancestor_chain())

    @property
    def level_name(self):
        return self.LEVEL_NAMES.get(self.depth, "未知层级")

    @property
    def full_path(self):
        return " > ".join(node.name for node in self.ancestor_chain())

    def has_active_path(self):
        return all(
            node.is_active and not node.is_deleted for node in self.ancestor_chain()
        )

    def descendant_ids(self, *, include_self=True):
        """Collect descendant ids without a tree dependency; depth is capped at three."""
        if not self.pk:
            return []
        found = {self.pk} if include_self else set()
        frontier = {self.pk}
        visited = set()
        while frontier:
            visited.update(frontier)
            children = set(
                type(self)
                .objects.filter(parent_id__in=frontier)
                .values_list("id", flat=True)
            )
            children -= visited
            found.update(children)
            frontier = children
        return sorted(found)

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
        super().clean()
        # 规范化
        if isinstance(self.code, str):
            self.code = self.code.strip().upper()
        if isinstance(self.name, str):
            self.name = self.name.strip()

        errors = {}
        if not self.code:
            errors["code"] = "分类编码不能为空"
        if not self.name:
            errors["name"] = "分类名称不能为空"

        if self.name:
            siblings = type(self).objects.filter(
                parent_id=self.parent_id,
                name__iexact=self.name,
            )
            if self.pk:
                siblings = siblings.exclude(pk=self.pk)
            if siblings.exists():
                errors["name"] = "同一上级分类下不能存在同名分类"

        # 应用层防环：不能把 parent 设为自己或子孙
        parent_depth = 0
        seen = {self.pk} if self.pk else set()
        node = self.parent
        while node is not None:
            if node.pk in seen:
                errors["parent"] = "上级分类不能是自身或自身的子孙（会形成环）"
                break
            if node.pk is not None:
                seen.add(node.pk)
            parent_depth += 1
            node = node.parent

        depth = parent_depth + 1
        if depth > self.MAX_DEPTH:
            errors["parent"] = "商品分类最多三级：大类、中类、小类"

        if self.parent_id and self.is_active and not self.parent.has_active_path():
            errors["parent"] = "启用分类不能挂在停用或已删除的上级分类下"

        if (
            self.pk
            and not self.is_active
            and type(self).objects.filter(parent_id=self.pk, is_active=True).exists()
        ):
            errors["is_active"] = "停用前请先停用所有直接下级分类"

        # Moving a populated subtree must not push any descendant beyond level three.
        if self.pk and "parent" not in errors:
            frontier = {self.pk}
            visited = {self.pk}
            descendant_distance = 0
            while frontier:
                children = set(
                    type(self)
                    .objects.filter(parent_id__in=frontier)
                    .values_list("id", flat=True)
                )
                children -= visited
                if not children:
                    break
                descendant_distance += 1
                visited.update(children)
                frontier = children
            if depth + descendant_distance > self.MAX_DEPTH:
                errors["parent"] = "移动后会使下级分类超过三级"

        if errors:
            raise ValidationError(errors)


class Brand(BaseModel):
    code = models.CharField("品牌编码", max_length=50, help_text="全局唯一")
    name = models.CharField("品牌名称", max_length=100)
    remark = models.CharField(
        "备注", max_length=255, blank=True
    )  # 如需 NULL -> null=True

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
        "单位编码",
        max_length=20,
        help_text="EA/PCS/CTN/PLT/KG/L 等",
        validators=[
            RegexValidator(
                r"^[A-Za-z0-9_\-\*]+$", "仅允许字母、数字、下划线、连字符、星号"
            )
        ],
    )
    name = models.CharField("单位名称", max_length=50)

    class Kind(models.TextChoices):
        COUNT = "COUNT", "计数"
        WEIGHT = "WEIGHT", "重量"
        VOLUME = "VOLUME", "体积"
        LENGTH = "LENGTH", "长度"
        AREA = "AREA", "面积"
        OTHER = "OTHER", "其他"

    kind = models.CharField(
        "类型", max_length=12, choices=Kind.choices, default=Kind.COUNT
    )

    # 小数位数：建议用 SmallInteger + 上界 6（按你系统的统一精度）
    decimal_places = models.PositiveSmallIntegerField(
        "小数位数", default=0, validators=[MaxValueValidator(6)]
    )

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
        AMBIENT = "AMBIENT", "常温"
        REFRIGERATED = "REFRIGERATED", "冷藏"
        FROZEN = "FROZEN", "冷冻"

    code = models.CharField("温区代码", max_length=20, help_text="温区代码")
    name = models.CharField("温区名称", max_length=50)

    # 允许负温度；加字段级范围校验防止离谱数据（按需调整上下限）
    min_temp = models.DecimalField(
        "最低温度(°C)",
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("-100.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    max_temp = models.DecimalField(
        "最高温度(°C)",
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("-100.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )

    storage_condition = models.CharField(
        "存储条件",
        max_length=20,
        blank=True,
        choices=StorageCondition.choices,
    )

    class Meta:
        verbose_name = "温区"
        verbose_name_plural = "温区"
        ordering = ["min_temp"]
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uniq_tempzone_code"),
            models.CheckConstraint(
                check=Q(min_temp__lte=F("max_temp")), name="chk_tempzone_min_le_max"
            ),
            # 可选：DB 端再兜一层合理范围（和字段 validators 二选一/都保留都行）
            # models.CheckConstraint(
            #     check=Q(min_temp__gte=Decimal("-100.00")) & Q(max_temp__lte=Decimal("100.00")),
            #     name="chk_tempzone_range",
            # ),
            models.CheckConstraint(
                check=~Q(code=""), name="chk_tempzone_code_not_empty"
            ),
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
            if sc == self.StorageCondition.AMBIENT and not (
                Decimal("10.00") <= lo and hi <= Decimal("30.00")
            ):
                raise ValidationError(
                    {"storage_condition": "常温建议区间约 10~30℃（请按内部标准调整）"}
                )
            if sc == self.StorageCondition.REFRIGERATED and not (
                Decimal("0.00") <= lo and hi <= Decimal("8.00")
            ):
                raise ValidationError(
                    {"storage_condition": "冷藏建议区间约 0~8℃（请按内部标准调整）"}
                )
            if sc == self.StorageCondition.FROZEN and not (
                Decimal("-30.00") <= lo and hi <= Decimal("0.00")
            ):
                raise ValidationError(
                    {"storage_condition": "冷冻建议区间约 -30~0℃（请按内部标准调整）"}
                )


# =========================主表：Product =========================
class Product(BaseModel):
    PACK_REQ_CHOICES = [
        ("NONE", "无（不需要打包）"),
        ("BAG", "袋装/气泡袋"),
        ("BOX", "装箱"),
        ("SHRINK", "缠绕/热缩"),
        ("PALLET", "打托/缠膜"),
    ]

    objects = ProductManager()
    all_objects = AllProductManager()

    owner = models.ForeignKey(
        Owner, on_delete=models.PROTECT, related_name="products", verbose_name="货主"
    )

    # 基本信息
    code = models.CharField("货主商品编码", max_length=50, help_text="货主内唯一")
    name = models.CharField("商品名称", max_length=200)
    spec = models.CharField("规格", max_length=200, blank=True, null=True)
    sku = models.CharField(
        "仓库SKU编码",
        max_length=50,
        blank=True,
        help_text="系统按“货主编码-序号”自动生成，货主内唯一",
    )
    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="分类",
        related_name="products",
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="品牌",
        related_name="products",
    )
    temperature_zone = models.ForeignKey(
        TemperatureZone,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="温度区域",
        related_name="products",
    )
    description = models.TextField("描述", blank=True, null=True)

    # 条码（更细可在 ProductPackage 中维护层级条码）
    gtin = models.CharField("标准贸易条码", max_length=20, blank=True, null=True)
    unit_barcode = models.CharField("零码", max_length=50, blank=True, null=True)
    carton_barcode = models.CharField("箱码", max_length=50, blank=True, null=True)
    carton_package = models.ForeignKey(
        "ProductPackage",
        on_delete=models.PROTECT,
        related_name="carton_barcode_products",
        verbose_name="箱码对应包装层级",
        blank=True,
        null=True,
        help_text="先创建商品包装层级，再将箱码与该商品的有效包装层级一次性绑定。",
    )

    pack_requirement = models.CharField(
        "打包要求",
        max_length=20,
        choices=PACK_REQ_CHOICES,
        default="NONE",
        db_index=True,
    )
    # （可选）补充自由备注，不想要就省略
    pack_note = models.CharField("打包备注", max_length=120, blank=True, default="")

    # 单位
    base_uom = models.ForeignKey(
        ProductUom,
        on_delete=models.PROTECT,
        related_name="as_base_of_products",
        verbose_name="基本单位",
    )

    # 拣配&补货策略
    class PickPolicy(models.TextChoices):
        AUTO = "AUTO", "优先整箱可用则整箱，否则散件"
        BASE_ONLY = "BASE_ONLY", "只允许散件"
        AUX_ONLY = "AUX_ONLY", "只允许整箱(不破箱)"
        OPTIMIZE = "OPTIMIZE", "按效率优化：整箱优先，尾差阈值内散拣"

    pick_policy = models.CharField(
        "拣配策略", max_length=12, choices=PickPolicy.choices, default=PickPolicy.AUTO
    )
    break_box_allowed = models.BooleanField("允许破箱", default=True)
    min_pick_multiple = models.PositiveIntegerField("最小拣配倍数(基本单位)", default=1)
    replenish_min = models.PositiveIntegerField("前置区补货下限(基本单位)", default=0)
    replenish_uom = models.ForeignKey(
        ProductUom,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="补货单位",
    )

    # 体积&重量（基本单位）
    volume = models.DecimalField(
        "体积(基本单位,m³)", max_digits=12, decimal_places=6, blank=True, null=True
    )
    weight = models.DecimalField(
        "重量(基本单位,kg)", max_digits=12, decimal_places=3, blank=True, null=True
    )
    net_content = models.DecimalField(
        "净含量(基本单位,g)", max_digits=12, decimal_places=3, blank=True, null=True
    )

    # 库存阈值（NULL 表示不设）
    min_stock = models.DecimalField(
        "最低库存(基本单位)", max_digits=12, decimal_places=2, blank=True, null=True
    )
    max_stock = models.DecimalField(
        "最高库存(基本单位)", max_digits=12, decimal_places=2, blank=True, null=True
    )

    # 批次 / 序列号 / 效期
    serial_control = models.BooleanField("序列号管理", default=False)
    batch_control = models.BooleanField("批次管理", default=True)
    expiry_control = models.BooleanField("保质期管理", default=True)

    product_image = models.ImageField(
        "商品图片", upload_to="products/", blank=True, null=True  # 按日期分目录存储
    )

    # 定价相关
    pricing_strategy = models.CharField(
        "定价策略",
        max_length=20,
        choices=[("WAC", "加权平均法"), ("NEW", "按最新批次")],
        default="WAC",  # 默认使用加权平均法
    )  # 定价策略字段
    price = models.DecimalField(
        "默认价格", max_digits=18, decimal_places=2, null=True, blank=True, default=None
    )  # 基本价格字段
    min_price = models.DecimalField(
        "最低价格", max_digits=10, decimal_places=2, blank=True, null=True
    )
    max_discount = models.DecimalField(
        "最高折扣%", max_digits=10, decimal_places=2, blank=True, null=True
    )

    class ExpiryBasis(models.TextChoices):
        MFG = "MFG", "生产日期"
        INBOUND = "INBOUND", "入库日期"

    expiry_basis = models.CharField(
        "效期基准",
        max_length=10,
        choices=ExpiryBasis.choices,
        blank=True,
        null=True,
        default="MFG",
    )

    shelf_life_days = models.PositiveIntegerField("保质期天数", blank=True, null=True)
    inbound_valid_days = models.PositiveIntegerField(
        "入库有效天数(入库基准)", blank=True, null=True
    )
    expiry_warning_days = models.PositiveIntegerField(
        "效期预警阈值(剩余天数)", blank=True, null=True
    )
    fefo_required = models.BooleanField("FEFO拣选(先到期先出)", default=True)
    mix_lot_allowed = models.BooleanField("允许库位混批", default=False)
    mix_expiry_allowed = models.BooleanField("允许库位混效期", default=False)

    # 温控（如采用多温区模型，可不使用下两字段）
    # temperature_min = models.DecimalField("最低温(°C)", max_digits=5, decimal_places=2, blank=True, null=True)
    # temperature_max = models.DecimalField("最高温(°C)", max_digits=5, decimal_places=2, blank=True, null=True)

    # 产地（ISO-3166-1 alpha-2）
    origin_country = models.CharField(
        "原产国(ISO-2)",
        max_length=2,
        blank=True,
        null=True,
        validators=[RegexValidator(r"^[A-Z]{2}$", "必须为两位大写字母的 ISO-2 代码")],
    )
    external_code = models.CharField(
        "外部系统商品编码", max_length=50, blank=True, null=True
    )
    extra = models.JSONField(
        "扩展属性", blank=True, null=False, default=dict
    )  # 建议默认空 dict
    material_quality = models.CharField("材质", max_length=20, blank=True, null=True)
    vender = models.CharField("厂家", max_length=50, blank=True, null=True)

    class Meta:
        verbose_name = "商品档案"
        verbose_name_plural = "商品档案"
        ordering = ["owner", "code"]
        permissions = [
            ("view_all_owner_products", "可查看所有货主商品"),
            ("manage_all_owner_products", "可处理所有货主商品"),
        ]
        constraints = [
            models.CheckConstraint(check=~Q(code=""), name="chk_prod_code_not_empty"),
            models.UniqueConstraint(fields=["owner", "code"], name="uniq_owner_code"),
            models.UniqueConstraint(fields=["owner", "sku"], name="uniq_owner_sku"),
            models.UniqueConstraint(fields=["owner", "gtin"], name="uniq_owner_gtin"),
            models.UniqueConstraint(
                fields=["owner", "unit_barcode"], name="uniq_owner_unit_barcode"
            ),
            models.UniqueConstraint(
                fields=["owner", "carton_barcode"], name="uniq_owner_carton_barcode"
            ),
            models.UniqueConstraint(
                fields=["owner", "external_code"], name="uniq_owner_external_code"
            ),
            # 关键业务约束（MySQL 8 支持 CHECK）
            models.CheckConstraint(
                check=Q(min_pick_multiple__gte=1), name="chk_min_pick_multiple_ge_1"
            ),
            # models.CheckConstraint(
            #     check=(Q(temperature_min__isnull=True) | Q(temperature_max__isnull=True) |
            #            Q(temperature_min__lte=models.F("temperature_max"))),
            #     name="chk_temp_min_le_max_or_null",
            # ),
            models.CheckConstraint(
                check=(
                    Q(min_stock__isnull=True)
                    | Q(max_stock__isnull=True)
                    | Q(min_stock__lt=models.F("max_stock"))
                ),
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
                    Q(expiry_control=False)
                    | (Q(expiry_basis="MFG") & Q(shelf_life_days__gt=0))
                    | (Q(expiry_basis="INBOUND") & Q(inbound_valid_days__gt=0))
                ),
                name="chk_expiry_days_valid",
            ),
            models.CheckConstraint(
                name="chk_expiry_warning_bounds",
                check=(
                    Q(expiry_control=False)
                    | Q(expiry_warning_days__isnull=True)
                    | (
                        (
                            Q(expiry_basis="MFG")
                            & Q(expiry_warning_days__gt=0)
                            & Q(expiry_warning_days__lt=models.F("shelf_life_days"))
                        )
                        | (
                            Q(expiry_basis="INBOUND")
                            & Q(expiry_warning_days__gt=0)
                            & Q(expiry_warning_days__lt=models.F("inbound_valid_days"))
                        )
                    )
                ),
            ),
        ]

        # 高价值查询索引（覆盖典型过滤/联表）
        indexes = [
            models.Index(fields=["owner", "is_active"]),
            models.Index(fields=["temperature_zone"]),
            models.Index(
                fields=["owner", "name"], name="owner_name_prefix_idx"
            ),  # LIKE 'xxx%' 前缀有效
            models.Index(fields=["category"]),
            models.Index(fields=["brand"]),
            models.Index(
                fields=["owner", "category", "is_active"], name="owner_category_active"
            ),
            models.Index(
                fields=["owner", "brand", "is_active"], name="owner_brand_active"
            ),
            models.Index(
                fields=["owner", "is_active", "batch_control"],
                name="owner_active_batch_idx",
            ),
            models.Index(
                fields=["owner", "is_active", "expiry_control"],
                name="owner_active_expiry_idx",
            ),
            # models.Index(fields=["owner", "is_active", "is_hazardous"], name="owner_active_hazard_idx"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("商品包含永久标识占用，不允许硬删除；请使用软删除。")

    def save(self, *args, **kwargs):
        """Save stable master data; identifier projections are service-managed."""
        adding = self._state.adding
        if adding and not self.owner_id:
            raise ValueError("新建商品时必须指定货主。")
        with transaction.atomic():
            owner = None
            validate_carton_binding = adding
            if adding:
                owner = (
                    Owner.all_objects.select_for_update()
                    .only("id", "code", "next_sku_sequence")
                    .get(pk=self.owner_id)
                )
                sequence = owner.next_sku_sequence
                while ProductIdentifierRegistry.objects.filter(
                    owner_id=owner.pk,
                    normalized_value=normalize_product_identifier(
                        f"{owner.code}-{sequence}"
                    ),
                ).exists():
                    sequence += 1
                self.sku = f"{owner.code}-{sequence}"
            else:
                original = (
                    type(self)
                    .all_objects.select_for_update()
                    .only(
                        "pk",
                        "owner_id",
                        "code",
                        "sku",
                        *LEGACY_PRODUCT_IDENTIFIER_FIELDS,
                        "carton_package_id",
                    )
                    .get(pk=self.pk)
                )
                stable_changes = []
                if self.owner_id != original.owner_id:
                    stable_changes.append("owner")
                for field in ("code", "sku"):
                    if normalize_product_identifier(
                        getattr(self, field)
                    ) != normalize_product_identifier(getattr(original, field)):
                        stable_changes.append(field)
                if stable_changes:
                    raise ValidationError(
                        {
                            stable_changes[
                                0
                            ]: "货主、货主商品编码和仓库SKU编码创建后不可修改。"
                        }
                    )

                projection_changes = [
                    field
                    for field in LEGACY_PRODUCT_IDENTIFIER_FIELDS
                    if normalize_product_identifier(getattr(self, field))
                    != normalize_product_identifier(getattr(original, field))
                ]
                if self.carton_package_id != original.carton_package_id:
                    projection_changes.append("carton_package")
                if projection_changes and not getattr(
                    self, "_allow_identifier_projection_update", False
                ):
                    field = projection_changes[0]
                    raise ValidationError(
                        {
                            field: "该字段是主值投影，不能直接修改；请通过条码或外部标识维护接口追加、设主或退役。"
                        }
                    )
                validate_carton_binding = bool(projection_changes)

            if validate_carton_binding and (
                self.carton_barcode or self.carton_package_id
            ):
                if not self.carton_barcode or not self.carton_package_id:
                    raise ValidationError(
                        {"carton_package": "箱码和箱码对应包装层级必须同时设置。"}
                    )
                package = ProductPackage.all_objects.select_related("product").get(
                    pk=self.carton_package_id
                )
                if not self.pk or package.product_id != self.pk:
                    raise ValidationError(
                        {"carton_package": "箱码对应包装层级必须属于当前商品。"}
                    )
                if package.is_deleted or not package.is_active:
                    raise ValidationError(
                        {"carton_package": "箱码对应包装层级必须启用且未删除。"}
                    )

            result = super().save(*args, **kwargs)

            if adding:
                Owner.all_objects.filter(pk=owner.pk).update(
                    next_sku_sequence=sequence + 1
                )
                owner.next_sku_sequence = sequence + 1
                if "owner" in self._state.fields_cache:
                    self._state.fields_cache["owner"] = owner

            if adding:
                from .identifier_services import bootstrap_product_identifiers

                bootstrap_product_identifiers(self)
            return result

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
            errors["code"] = "货主商品编码不能为空"

        if self._state.adding and not self.category_id:
            errors["category"] = "新建商品时至少需要选择一个大类"
        elif self.pk:
            original_category_id = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values_list("category_id", flat=True)
                .first()
            )
            if original_category_id and not self.category_id:
                errors["category"] = "已分类商品不能清空分类"

        if self.category_id and not self.category.has_active_path():
            errors["category"] = "商品只能选择分类链全部启用的分类"

        # 新商品的 SKU 由 save() 统一分配，忽略任何调用方传入的临时值。
        # 已有商品只去除两端空白，避免货主代码含小写时更新其他字段误改 SKU。
        if self._state.adding:
            self.sku = ""
        elif self.sku:
            self.sku = self.sku.strip()

        for f in ["gtin", "unit_barcode", "carton_barcode", "external_code"]:
            v = getattr(self, f, None)
            if isinstance(v, str):
                setattr(self, f, v.strip() or None)

        original_binding = None
        if self.pk:
            original_binding = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values("carton_barcode", "carton_package_id")
                .first()
            )
        binding_changed = original_binding is None or (
            normalize_product_identifier(self.carton_barcode)
            != normalize_product_identifier(original_binding["carton_barcode"])
            or self.carton_package_id != original_binding["carton_package_id"]
        )
        if binding_changed and bool(self.carton_barcode) != bool(
            self.carton_package_id
        ):
            errors["carton_package"] = "箱码和箱码对应包装层级必须同时设置。"
        elif binding_changed and self.carton_package_id:
            package = ProductPackage.all_objects.filter(
                pk=self.carton_package_id
            ).first()
            if package is None or (self.pk and package.product_id != self.pk):
                errors["carton_package"] = "箱码对应包装层级必须属于当前商品。"
            elif package.is_deleted or not package.is_active:
                errors["carton_package"] = "箱码对应包装层级必须启用且未删除。"

        if self.origin_country:
            self.origin_country = self.origin_country.upper()

        self.shelf_life_days = _as_pos_int(self.shelf_life_days)
        self.inbound_valid_days = _as_pos_int(self.inbound_valid_days)
        self.expiry_warning_days = _as_pos_int(self.expiry_warning_days)

        # GTIN（默认宽松；如启用校验位则做 Mod10 校验）
        if self.gtin:
            if not self.gtin.isdigit() or len(self.gtin) not in (8, 12, 13, 14):
                errors["gtin"] = "标准贸易条码必须为8/12/13/14位数字"
            elif ENABLE_GTIN_CHECK_DIGIT and not _gtin_mod10_is_valid(self.gtin):
                errors["gtin"] = "标准贸易条码校验位不通过（GS1 Mod10）"

        # 库存上下限
        if (
            self.min_stock is not None
            and self.max_stock is not None
            and self.min_stock >= self.max_stock
        ):
            errors["min_stock"] = (
                "最低库存必须小于最高库存（或将最高库存留空表示不设上限）。"
            )

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
                    if (
                        self.expiry_warning_days <= 0
                        or self.expiry_warning_days >= self.shelf_life_days
                    ):
                        errors["expiry_warning_days"] = (
                            "预警天数必须在 1 ~ 保质期天数-1 之间。"
                        )
            if self.expiry_basis == "INBOUND":
                if not self.inbound_valid_days or self.inbound_valid_days <= 0:
                    errors["inbound_valid_days"] = (
                        "按入库日期管理时，入库有效天数必须 > 0。"
                    )
                if self.expiry_warning_days is not None:
                    if (
                        self.expiry_warning_days <= 0
                        or self.expiry_warning_days >= self.inbound_valid_days
                    ):
                        errors["expiry_warning_days"] = (
                            "预警天数必须在 1 ~ 入库有效天数-1 之间。"
                        )
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
                    errors[field] = (
                        f"该货主下{label}“{val}”已存在（{conflict.code}-{conflict.name}）。"
                    )

        _uniq_owner_field("code", "货主商品编码")
        _uniq_owner_field("sku", "仓库SKU编码")
        _uniq_owner_field("gtin", "标准贸易条码")
        _uniq_owner_field("unit_barcode", "零码")
        _uniq_owner_field("carton_barcode", "箱码")
        _uniq_owner_field("external_code", "外部系统商品编码")

        # 六类标识共享一个货主级命名空间；同一商品内部重复合法。
        if self.owner_id:
            for field in PRODUCT_IDENTIFIER_FIELDS:
                value = normalize_product_identifier(getattr(self, field, None))
                if not value:
                    continue
                conflict = ProductIdentifierRegistry.objects.filter(
                    owner_id=self.owner_id,
                    normalized_value=value,
                )
                if self.pk:
                    conflict = conflict.exclude(product_id=self.pk)
                registry = conflict.select_related("product").first()
                if registry:
                    product = registry.product
                    deleted = "（已软删除）" if product.is_deleted else ""
                    source = f"商品 {product.code}-{product.name}{deleted}"
                    errors[field] = f"该货主下标识“{value}”已被{source}占用。"

        if self.pk:
            orig = (
                type(self).all_objects.only("owner_id", "code", "sku").get(pk=self.pk)
            )

            def _norm(x):
                if isinstance(x, str):
                    x = x.strip()
                    return x or None
                return x

            changed = ["owner"] if self.owner_id != orig.owner_id else []
            for f in ["code", "sku"]:
                if _norm(getattr(self, f)) != _norm(getattr(orig, f)):
                    changed.append(f)

            if changed:
                errors[changed[0]] = f"禁止修改稳定主数据字段：{', '.join(changed)}。"

        if errors:
            raise ValidationError(errors)


class ProductIdentifierRegistry(models.Model):
    """Maps one normalized owner-wide identifier to exactly one product."""

    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name="product_identifier_registry",
        verbose_name="货主",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="identifier_registry",
        verbose_name="商品",
    )
    normalized_value = models.CharField("标准化标识值", max_length=50)

    class Meta:
        verbose_name = "商品标识注册项"
        verbose_name_plural = "商品标识注册项"
        indexes = [
            models.Index(fields=["normalized_value"], name="prod_ident_norm_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_value"],
                name="uniq_owner_product_identifier",
            ),
        ]

    @classmethod
    def values_for_product(cls, product) -> set[str]:
        values = {
            normalize_product_identifier(product.code),
            normalize_product_identifier(product.sku),
        }
        values.update(
            ProductBarcode.all_objects.filter(product=product).values_list(
                "normalized_value", flat=True
            )
        )
        values.update(
            ProductExternalIdentifier.all_objects.filter(product=product).values_list(
                "normalized_value", flat=True
            )
        )
        return {value for value in values if value}

    def __str__(self):
        return f"{self.owner_id}:{self.normalized_value} -> {self.product_id}"


class ProductBarcode(BaseModel):
    class BarcodeType(models.TextChoices):
        GTIN = "GTIN", "标准贸易条码"
        UNIT = "UNIT", "零码"
        CARTON = "CARTON", "箱码"
        PACKAGE = "PACKAGE", "包装层级条码"
        OTHER = "OTHER", "其他条码"

    objects = ProductBarcodeQuerySet.as_manager()
    all_objects = models.Manager.from_queryset(ProductBarcodeQuerySet)()

    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name="product_barcodes",
        verbose_name="货主",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="barcodes", verbose_name="商品"
    )
    barcode = models.CharField("条码", max_length=50)
    normalized_value = models.CharField("标准化条码", max_length=50, editable=False)
    barcode_type = models.CharField(
        "条码类型", max_length=12, choices=BarcodeType.choices
    )
    package = models.ForeignKey(
        "ProductPackage",
        on_delete=models.PROTECT,
        related_name="barcode_records",
        blank=True,
        null=True,
        verbose_name="包装层级",
    )
    qty_in_base = models.PositiveIntegerField("基础单位换算快照", default=1)
    is_primary = models.BooleanField("主条码", default=False)
    primary_scope = models.CharField(
        "主码唯一范围", max_length=40, blank=True, null=True, editable=False
    )
    valid_from = models.DateTimeField("生效时间", blank=True, null=True)
    valid_to = models.DateTimeField("失效时间", blank=True, null=True)

    class Meta:
        verbose_name = "商品条码"
        verbose_name_plural = "商品条码"
        ordering = ("product", "barcode_type", "package_id", "-is_primary", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "primary_scope"],
                name="uniq_primary_product_barcode_scope",
            ),
            models.CheckConstraint(
                check=Q(qty_in_base__gt=0), name="chk_product_barcode_qty_gt_0"
            ),
            models.CheckConstraint(
                check=Q(valid_from__isnull=True)
                | Q(valid_to__isnull=True)
                | Q(valid_from__lte=F("valid_to")),
                name="chk_product_barcode_valid_range",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "normalized_value"]),
            models.Index(fields=["product", "barcode_type", "is_active"]),
            models.Index(fields=["normalized_value"], name="prod_barcode_norm_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        self.barcode = (self.barcode or "").strip()
        self.normalized_value = normalize_product_identifier(self.barcode)
        if not self.normalized_value:
            errors["barcode"] = "条码不能为空。"
        if self.barcode_type == self.BarcodeType.GTIN and self.barcode:
            if not self.barcode.isdigit() or len(self.barcode) not in (8, 12, 13, 14):
                errors["barcode"] = "标准贸易条码必须为8/12/13/14位数字。"
        if self.product_id and not self.owner_id:
            self.owner_id = self.product.owner_id
        elif self.product_id and self.owner_id != self.product.owner_id:
            errors["owner"] = "条码货主必须与商品货主一致。"
        if self.barcode_type == self.BarcodeType.UNIT:
            if self.package_id:
                errors["package"] = "零码不得关联包装层级。"
            self.qty_in_base = 1
        elif self.barcode_type in {self.BarcodeType.CARTON, self.BarcodeType.PACKAGE}:
            if not self.package_id:
                errors["package"] = "箱码和包装层级条码必须关联包装层级。"
            elif self.package.product_id != self.product_id:
                errors["package"] = "包装层级必须属于当前商品。"
            elif self.package.is_deleted or not self.package.is_active:
                errors["package"] = "不能绑定停用或已删除的包装层级。"
            elif self._state.adding:
                self.qty_in_base = self.package.qty_in_base
        elif self.package_id:
            errors["package"] = "该条码类型不得关联包装层级。"
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            errors["valid_to"] = "失效时间不能早于生效时间。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not getattr(self, "_identifier_service_write", False):
            raise ValidationError("商品条码必须通过统一条码维护服务保存。")
        self.primary_scope = (
            f"{self.barcode_type}:{self.package_id or 0}" if self.is_primary else None
        )
        if self.pk:
            original = (
                type(self)
                .all_objects.only(
                    "owner_id",
                    "product_id",
                    "barcode",
                    "normalized_value",
                    "barcode_type",
                    "package_id",
                    "qty_in_base",
                )
                .get(pk=self.pk)
            )
            immutable = (
                self.owner_id,
                self.product_id,
                self.barcode,
                self.normalized_value,
                self.barcode_type,
                self.package_id,
                self.qty_in_base,
            )
            old = (
                original.owner_id,
                original.product_id,
                original.barcode,
                original.normalized_value,
                original.barcode_type,
                original.package_id,
                original.qty_in_base,
            )
            if immutable != old:
                raise ValidationError(
                    "条码、商品、类型、包装和换算快照创建后不可修改。"
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("商品条码历史不得硬删除；请执行退役操作。")

    def is_effective(self, at=None):
        at = at or timezone.now()
        return (
            self.is_active
            and not self.is_deleted
            and (self.valid_from is None or self.valid_from <= at)
            and (self.valid_to is None or self.valid_to >= at)
        )


class ProductExternalIdentifier(BaseModel):
    objects = ProductExternalIdentifierQuerySet.as_manager()
    all_objects = models.Manager.from_queryset(ProductExternalIdentifierQuerySet)()

    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name="product_external_identifiers",
        verbose_name="货主",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="external_identifiers",
        verbose_name="商品",
    )
    source_system = models.CharField("来源系统", max_length=50)
    external_code = models.CharField("外部系统商品编码", max_length=50)
    normalized_value = models.CharField("标准化外部编码", max_length=50, editable=False)
    is_primary = models.BooleanField("主标识", default=False)
    primary_scope = models.CharField(
        "主标识唯一范围", max_length=50, blank=True, null=True, editable=False
    )
    valid_from = models.DateTimeField("生效时间", blank=True, null=True)
    valid_to = models.DateTimeField("失效时间", blank=True, null=True)

    class Meta:
        verbose_name = "商品外部标识"
        verbose_name_plural = "商品外部标识"
        ordering = ("product", "source_system", "-is_primary", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "primary_scope"],
                name="uniq_primary_external_identifier_source",
            ),
            models.CheckConstraint(
                check=Q(valid_from__isnull=True)
                | Q(valid_to__isnull=True)
                | Q(valid_from__lte=F("valid_to")),
                name="chk_external_identifier_valid_range",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "normalized_value"]),
            models.Index(fields=["product", "source_system", "is_active"]),
            models.Index(fields=["normalized_value"], name="prod_extident_norm_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        self.source_system = normalize_product_identifier(self.source_system)
        self.external_code = (self.external_code or "").strip()
        self.normalized_value = normalize_product_identifier(self.external_code)
        if not self.source_system:
            errors["source_system"] = "来源系统不能为空。"
        if not self.normalized_value:
            errors["external_code"] = "外部系统商品编码不能为空。"
        if self.product_id and not self.owner_id:
            self.owner_id = self.product.owner_id
        elif self.product_id and self.owner_id != self.product.owner_id:
            errors["owner"] = "外部标识货主必须与商品货主一致。"
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            errors["valid_to"] = "失效时间不能早于生效时间。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not getattr(self, "_identifier_service_write", False):
            raise ValidationError("外部标识必须通过统一标识维护服务保存。")
        self.primary_scope = self.source_system if self.is_primary else None
        if self.pk:
            original = (
                type(self)
                .all_objects.only(
                    "owner_id",
                    "product_id",
                    "source_system",
                    "external_code",
                    "normalized_value",
                )
                .get(pk=self.pk)
            )
            if (
                self.owner_id,
                self.product_id,
                self.source_system,
                self.external_code,
                self.normalized_value,
            ) != (
                original.owner_id,
                original.product_id,
                original.source_system,
                original.external_code,
                original.normalized_value,
            ):
                raise ValidationError("商品、来源系统和外部编码创建后不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("外部标识历史不得硬删除；请执行退役操作。")

    def is_effective(self, at=None):
        at = at or timezone.now()
        return (
            self.is_active
            and not self.is_deleted
            and (self.valid_from is None or self.valid_from <= at)
            and (self.valid_to is None or self.valid_to >= at)
        )


# ========================= 商品 × 包装层级# =========================
class ProductPackage(BaseModel):
    objects = ProductPackageManager()
    all_objects = AllProductPackageManager()

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="packages",
        verbose_name="商品",
        related_query_name="product_package",
    )
    uom = models.ForeignKey(
        ProductUom,
        on_delete=models.PROTECT,
        related_name="packages",
        verbose_name="包装单位",
    )
    qty_in_base = models.PositiveIntegerField("换算数量")

    barcode = models.CharField("层级条码", max_length=50, blank=True, null=True)
    length_cm = models.DecimalField(
        "长(cm)", max_digits=8, decimal_places=2, blank=True, null=True
    )
    width_cm = models.DecimalField(
        "宽(cm)", max_digits=8, decimal_places=2, blank=True, null=True
    )
    height_cm = models.DecimalField(
        "高(cm)", max_digits=8, decimal_places=2, blank=True, null=True
    )
    gross_weight_kg = models.DecimalField(
        "毛重(kg)", max_digits=10, decimal_places=3, blank=True, null=True
    )
    volume_m3 = models.DecimalField(
        "体积(m³)", max_digits=12, decimal_places=6, blank=True, null=True
    )
    volume_auto = models.BooleanField("体积自动计算", default=True)

    class VolumeStatus(models.TextChoices):
        NONE = "", "未校验"
        CALCULATED = "CALCULATED", "已计算"
        MISMATCH = "MISMATCH", "手输与计算不一致"

    volume_m3_status = models.CharField(
        "体积状态",
        max_length=12,
        choices=VolumeStatus.choices,
        default=VolumeStatus.NONE,
        blank=True,
    )

    is_pickable = models.BooleanField("可直接拣配", default=False)
    is_purchase_default = models.BooleanField(
        "采购辅助单位", null=True, blank=True, default=None
    )
    is_sales_default = models.BooleanField(
        "销售辅助单位", null=True, blank=True, default=None
    )

    sort_order = models.PositiveIntegerField("排序", default=0)

    class Meta:
        verbose_name = "商品包装层级"
        verbose_name_plural = "商品包装层级"
        ordering = ["product", "sort_order", "uom"]  # ✅ 用字段名
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
            models.CheckConstraint(
                check=Q(qty_in_base__gt=0), name="chk_qty_in_base_gt_0"
            ),
            # 尺寸三者要么都为空，要么都 >0（可按需保留）
            models.CheckConstraint(
                name="chk_dims_all_or_none",
                check=(
                    (
                        Q(length_cm__isnull=True)
                        & Q(width_cm__isnull=True)
                        & Q(height_cm__isnull=True)
                    )
                    | (Q(length_cm__gt=0) & Q(width_cm__gt=0) & Q(height_cm__gt=0))
                ),
            ),
            # 体积、毛重非负（NULL 允许）
            models.CheckConstraint(
                name="chk_nonneg_weight_volume",
                check=(Q(gross_weight_kg__isnull=True) | Q(gross_weight_kg__gte=0))
                & (Q(volume_m3__isnull=True) | Q(volume_m3__gte=0)),
            ),
            # 可选：同一商品同条码唯一（允许多个 NULL）
            models.UniqueConstraint(
                fields=["product", "barcode"], name="uni_pkg_pro_barcode"
            ),
        ]
        indexes = [
            models.Index(fields=["product", "uom"]),  # ✅
            models.Index(
                fields=["product", "sort_order"], name="idx_pkg_prod_sort"
            ),  # ✅ 去重
            models.Index(fields=["barcode"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        # 注意：大量列表渲染时请 select_related("product__base_uom", "uom")
        return f"{self.product.code} - 1 {self.uom.code} = {self.qty_in_base} {self.product.base_uom.code}"

    def save(self, *args, **kwargs):
        # ✅ 只做“自动计算/赋值”，不要在 save() 里 full_clean()（否则 admin 容易 500）
        adding = self._state.adding
        if self.volume_auto:
            if (
                (self.length_cm and self.length_cm > 0)
                and (self.width_cm and self.width_cm > 0)
                and (self.height_cm and self.height_cm > 0)
            ):
                calc = (self.length_cm * self.width_cm * self.height_cm) / Decimal(
                    "1000000"
                )
                calc_q = calc.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                if self.volume_m3 is None:
                    self.volume_m3 = calc_q
                    self.volume_m3_status = self.VolumeStatus.CALCULATED
                else:
                    tol = max(
                        Decimal("0.000001"), calc_q * Decimal("0.001")
                    )  # 容差 max(1e-6, 0.1%)
                    if (self.volume_m3 - calc_q).copy_abs() > tol:
                        self.volume_m3_status = self.VolumeStatus.MISMATCH
                    else:
                        self.volume_m3 = calc_q
                        self.volume_m3_status = self.VolumeStatus.CALCULATED
            else:
                self.volume_m3_status = self.VolumeStatus.NONE
                self.volume_m3 = None

        with transaction.atomic():
            if self._state.adding:
                if not self.product_id:
                    raise ValueError("新建商品包装层级时必须指定商品。")
            else:
                original = (
                    type(self)
                    .all_objects.select_for_update()
                    .only("pk", "product_id", "barcode", "is_active", "is_deleted")
                    .get(pk=self.pk)
                )
                if normalize_product_identifier(
                    self.barcode
                ) != normalize_product_identifier(original.barcode) and not getattr(
                    self, "_allow_identifier_projection_update", False
                ):
                    raise ValidationError(
                        {
                            "barcode": "该字段是主值投影，不能直接修改；请通过商品条码维护接口追加、设主或退役。"
                        }
                    )
                if Product.all_objects.filter(carton_package_id=self.pk).exists() and (
                    self.product_id != original.product_id
                    or self.is_deleted
                    or not self.is_active
                ):
                    raise ValidationError(
                        "该包装层级已绑定商品箱码，不能转移、停用或删除。"
                    )
            result = super().save(*args, **kwargs)
            if adding and self.barcode:
                from .identifier_services import bootstrap_package_identifier

                bootstrap_package_identifier(self)
            return result

    def clean(self):
        super().clean()
        errors = {}

        # --- 规范化 ---
        if isinstance(self.barcode, str):
            self.barcode = self.barcode.strip() or None

        if self.pk and Product.all_objects.filter(carton_package_id=self.pk).exists():
            original = (
                type(self).all_objects.filter(pk=self.pk).only("product_id").first()
            )
            if original and (
                self.product_id != original.product_id
                or self.is_deleted
                or not self.is_active
            ):
                errors["__all__"] = "该包装层级已绑定商品箱码，不能转移、停用或删除。"

        # --- 基础校验 ---
        if not self.uom_id:
            errors["uom"] = "请选择包装单位。"
        if (self.qty_in_base or 0) <= 0:
            errors["qty_in_base"] = "换算数量必须 > 0。"

        # 与基本单位相同则必须 1:1
        if (
            self.uom_id
            and self.product_id
            and self.uom_id == self.product.base_uom_id
            and self.qty_in_base != 1
        ):
            errors["qty_in_base"] = "基础单位层级的换算数必须为 1。"

        # --- ✅ 关键：把“会导致 500 的唯一性错误”提前到 clean()，让 admin 当作表单错误显示 ---
        if self.product_id and self.uom_id:
            qs = type(self).objects.filter(
                product_id=self.product_id, uom_id=self.uom_id, is_deleted=False
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                # 放在 __all__，效果与 Django 默认“字段组合已存在”类似
                errors["__all__"] = "包含 商品 和 包装单位 的 商品包装层级 已经存在。"

        if self.product_id and self.barcode:
            qs = type(self).objects.filter(
                product_id=self.product_id, barcode=self.barcode, is_deleted=False
            )
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                errors["barcode"] = "该商品下此层级条码已存在。"

            normalized = normalize_product_identifier(self.barcode)
            registry_qs = ProductIdentifierRegistry.objects.filter(
                owner_id=self.product.owner_id,
                normalized_value=normalized,
            )
            if self.pk:
                registry_qs = registry_qs.exclude(product_id=self.product_id)
            registry = registry_qs.select_related("product").first()
            if registry:
                deleted = "（商品已软删除）" if registry.product.is_deleted else ""
                source = f"商品标识 {registry.product.code}{deleted}"
                errors["barcode"] = f"该货主下标识“{normalized}”已被{source}占用。"

        # 每商品的“默认单位”唯一（应用层校验；并发场景仍由 DB 约束兜底）
        for flag, label in (
            ("is_purchase_default", "采购"),
            ("is_sales_default", "销售"),
        ):
            if getattr(self, flag):
                qs = type(self).objects.filter(
                    product_id=self.product_id, **{flag: True}, is_deleted=False
                )
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
