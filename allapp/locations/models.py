from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal

from allapp.core.models import BaseModel
from django.utils.translation import gettext_lazy as _
from allapp.core.choices import ZoneType

class Warehouse(BaseModel):
    code = models.CharField("仓库编号", max_length=10, unique=True)
    name = models.CharField("仓库名称", max_length=30)
    class Meta:
        verbose_name = "仓库"
        verbose_name_plural = "仓库"
        ordering = ["code"]
    def __str__(self): return f"{self.name}"

class Subwarehouse(BaseModel):
    warehouse = models.ForeignKey("Warehouse", verbose_name="所属仓库", on_delete=models.PROTECT,
                                  related_name="subwarehouse")
    code = models.CharField("仓库编号", max_length=10, unique=True)
    name = models.CharField("仓库名称", max_length=30)
    floor_no = models.PositiveSmallIntegerField(_("楼层"), default=1, db_index=True)
    class Meta:
        verbose_name = "仓库"
        verbose_name_plural = "仓库"
        ordering = ["code"]
    def __str__(self): return f"{self.name}"

    def clean(self):
        super().clean()
        if not self.warehouse_id:
            raise ValidationError({"warehouse": "必须明确指定所属仓库"})

    def save(self, *args, **kwargs):
        # 先执行一次 clean，让 code/subwarehouse 能补齐 warehouse，
        # 避免 full_clean() 在 clean_fields 阶段先因为 warehouse=None 失败。
        self.clean()
        self.full_clean()
        return super().save(*args, **kwargs)

class Location(BaseModel):

    warehouse = models.ForeignKey("Warehouse", verbose_name="所属仓库", on_delete=models.PROTECT, related_name="locations")
    subwarehouse = models.ForeignKey("Subwarehouse", verbose_name="所属仓", on_delete=models.PROTECT,
                                  related_name="locations", blank=True, null=True)
    zone_type = models.PositiveSmallIntegerField(
        _("区域类型"), choices=ZoneType.choices, default=ZoneType.STORAGE, db_index=True
    )
    # zone = models.ForeignKey("Zone", verbose_name="所属分区", on_delete=models.PROTECT, related_name="locations")

    # 位置编码：合并了原有的货架、货架层、列号等信息
    code = models.CharField("储位编码", max_length=60)
    name = models.CharField("储位名称", max_length=100, blank=True, null=True)
    barcode = models.CharField("储位条码", max_length=64, blank=True, null=True)
    max_volume_m3 = models.DecimalField("最大体积(m³)", max_digits=12, decimal_places=3, blank=True, null=True)
    max_weight_kg = models.DecimalField("最大承重(kg)", max_digits=12, decimal_places=2, blank=True, null=True)
    is_disabled = models.BooleanField("禁用", default=False)
    is_frozen = models.BooleanField("冻结", default=False)

    # 通过编码获得的层级信息
    rack_code = models.CharField("货架编码", max_length=10, blank=True, null=True)
    rack_level_code = models.CharField("货架层编码", max_length=10, blank=True, null=True)
    level_code = models.CharField("层号", max_length=10, blank=True, null=True)
    col_no = models.CharField("列号", max_length=10, blank=True, null=True)
    slot_no = models.CharField("位号", max_length=10, blank=True, null=True)
    product_categories = models.ManyToManyField("products.ProductCategory", verbose_name="存放商品类别", blank=True, related_name="locations")
    batch_guide = models.CharField("批量设置指引", max_length=200, blank=True, null=True)

    class Meta:
        verbose_name = "储位"
        verbose_name_plural = "储位管理"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "code"], name="ux_loc_wh_code"),
        ]
        indexes = [
            # 常见过滤：按子仓 + 区类型（拣选/存储…）
            models.Index(fields=["subwarehouse", "zone_type"], name="ix_loc_sw_zt"),

            # 常见过滤：按子仓 + 启用状态 / 冻结状态（布尔放在末位）
            models.Index(fields=["subwarehouse", "is_disabled"], name="ix_loc_sw_dis"),
            models.Index(fields=["subwarehouse", "is_frozen"], name="ix_loc_sw_fro"),

            # 若经常用条码/编码直接定位（扫描、精确或前缀查询）
            models.Index(fields=["code"], name="ix_loc_code"),  # 仅当经常按 code alone 查
            models.Index(fields=["barcode"], name="ix_loc_barcode"),  # 扫码用；若全局唯一可用 UniqueConstraint
        ]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        errors = {}

        # 校验并解析编码（例：WH01-Z01-R01-01-02）
        if self.code:
            parts = self.code.split('-')
            if len(parts) != 4:
                raise ValidationError("储位编码格式不正确，应为[仓号]-[层号]-[列号]-[位号]")

            # 解析编码并填充对应字段
            # self. = parts[0]  # 货架编码
            subwarehouse_no=parts[0]  # 子仓号
            sw = Subwarehouse.objects.filter(code=subwarehouse_no).select_related("warehouse").first()
            if not sw:
               raise ValidationError({"subwarehouse": "该子仓不存在，请先建立子仓"})
            self.subwarehouse = sw
            if not self.warehouse_id:
                self.warehouse = sw.warehouse
            self.level_code = parts[1]  # 货架层编码
            self.col_no = parts[2]  # 列号
            self.slot_no = parts[3]  # 位号
            self.barcode=self.code

        if self.subwarehouse_id:
            if not self.warehouse_id:
                self.warehouse_id = self.subwarehouse.warehouse_id
            elif self.warehouse_id != self.subwarehouse.warehouse_id:
                errors["warehouse"] = "所属仓库必须与子仓一致"

        if not self.warehouse_id:
            errors["warehouse"] = "必须明确指定所属仓库"

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # 先执行一次 clean，让 code/subwarehouse 能补齐 warehouse，
        # 避免 full_clean() 在 clean_fields 阶段先因为 warehouse=None 失败。
        self.clean()
        self.full_clean()
        return super().save(*args, **kwargs)

class Container(BaseModel):
    TYPES = [("TOTE", "料箱"), ("CARTON", "纸箱"), ("PALLET", "托盘")]

    # 内联的枚举（不单独定义类/常量）
    class Scope(models.TextChoices):
        PRIVATE = "PRIVATE", "私有（按货主）"
        PUBLIC  = "PUBLIC",  "公共（仓库）"

    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, verbose_name="仓库")
    owner     = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, null=True, blank=True, verbose_name="货主")

    scope     = models.CharField("范围", max_length=16, choices=Scope.choices, default=Scope.PRIVATE)

    container_no   = models.CharField("容器号", max_length=60)
    container_type = models.CharField("容器类型", max_length=10, choices=TYPES, default="CARTON")

    location = models.ForeignKey("locations.Location", on_delete=models.PROTECT, null=True, blank=True, verbose_name="当前位置")
    parent   = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children", verbose_name="上级容器")

    length_cm = models.DecimalField("长(cm)", max_digits=10, decimal_places=2, null=True, blank=True)
    width_cm  = models.DecimalField("宽(cm)", max_digits=10, decimal_places=2, null=True, blank=True)
    height_cm = models.DecimalField("高(cm)", max_digits=10, decimal_places=2, null=True, blank=True)
    tare_kg   = models.DecimalField("皮重(kg)", max_digits=10, decimal_places=3, null=True, blank=True)
    max_gross_kg = models.DecimalField("最大毛重(kg)", max_digits=12, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = "容器"
        verbose_name_plural = "容器"
        constraints = [
            # 同仓+容器号唯一（公共/私有都适用）
            models.UniqueConstraint(fields=["warehouse", "container_no"], name="ux_container_wh_no"),
        ]
        indexes = [
            models.Index(fields=["owner", "warehouse"], name="ix_cont_own_wh"),
            models.Index(fields=["parent"], name="ix_cont_parent"),
        ]

    def __str__(self):
        return f"{self.container_no}"

    def _sync_warehouse_from_relations(self):
        if self.location_id and not self.warehouse_id:
            self.warehouse_id = self.location.warehouse_id
        if self.parent_id and not self.warehouse_id:
            self.warehouse_id = self.parent.warehouse_id

    # 可选：简易承载能力计算
    def gross_limit_kg(self):
        return self.max_gross_kg or Decimal("0")

    def clean(self):
        super().clean()
        self._sync_warehouse_from_relations()
        if not self.warehouse_id:
            raise ValidationError({"warehouse": "必须明确指定容器仓库"})
        # 位置与仓一致
        if self.location_id and self.location.warehouse_id != self.warehouse_id:
            raise ValidationError({"location": "当前位置的仓库必须与容器仓库一致"})

        # 父容器与本容器同仓（可选：也可要求同 owner）
        if self.parent_id and self.parent.warehouse_id != self.warehouse_id:
            raise ValidationError({"parent": "上级容器必须与本容器同仓"})
        # if self.parent_id and self.parent.owner_id != self.owner_id:
        #     raise ValidationError({"parent": "上级容器必须与本容器同货主"})

        # 公共/私有与 owner 一致性
        if self.scope == Container.Scope.PUBLIC and self.owner_id:
            raise ValidationError({"owner": "公共容器不应绑定货主"})
        if self.scope == Container.Scope.PRIVATE and not self.owner_id:
            raise ValidationError({"owner": "私有容器必须绑定货主"})

        # 防止循环嵌套
        if self.parent_id:
            current = self.parent
            while current:
                if current.id == self.id:
                    raise ValidationError("容器不能嵌套自身")
                current = current.parent

    def save(self, *args, **kwargs):
        self._sync_warehouse_from_relations()
        self.full_clean()
        return super().save(*args, **kwargs)



