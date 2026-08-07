# core/models/base.py
import json
from datetime import date

from django.conf import settings
from django.db import IntegrityError, models, transaction


class _SkipCleanFlag:
    """简易的线程内标记；避免全局共享（够用且不引入依赖）。"""

    _local = {}

    @classmethod
    def get(cls, key):
        return cls._local.get(key, False)

    @classmethod
    def set(cls, key, val):
        cls._local[key] = bool(val)


class SoftDeleteMixin(models.Model):
    is_deleted = models.BooleanField("已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", blank=True, null=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="删除人",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="%(class)s_deleted",
    )

    class Meta:
        abstract = True

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def restore(self, **conds):
        """
        批量恢复软删记录：
        用法：MyModel.objects.restore(pk=1) 或 code__in=[...]
        """
        # 用全量管理器包含已删记录；如果模型没定义 all_objects，则退回 _base_manager
        all_mgr = getattr(self.model, "all_objects", None) or self.model._base_manager
        return all_mgr.filter(is_deleted=True, **conds).update(
            is_deleted=False, deleted_at=None, deleted_by=None
        )


class TimeStampedMixin(models.Model):
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True


class UserStampedMixin(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="更新人",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="%(class)s_updated",
    )

    class Meta:
        abstract = True


class BaseModel(UserStampedMixin, TimeStampedMixin, SoftDeleteMixin):
    is_active = models.BooleanField("启用状态", default=True)
    remark = models.CharField("备注", max_length=200, blank=True, null=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True


class SystemSetting(TimeStampedMixin):
    class ValueType(models.TextChoices):
        STRING = "string", "文本"
        BOOLEAN = "boolean", "布尔"
        INTEGER = "integer", "整数"
        DECIMAL = "decimal", "小数"
        JSON = "json", "JSON"

    POS_NAMESPACE = "pos"
    POS_SALE_PRINT_METHOD_KEY = "sale_print_method"
    POS_SALE_PRINT_CONFIG_KEY = "sale_print_config"
    POS_SALE_PRINT_FRONTEND = "frontend_html"
    POS_SALE_PRINT_BACKEND = "backend_html"
    POS_DEFAULT_SALE_PRINT_CONFIG = "pos_dot_241_93"

    namespace = models.CharField(
        "命名空间", max_length=50, default="global", db_index=True
    )
    key = models.CharField("配置键", max_length=100)
    name = models.CharField("名称", max_length=100)
    value_type = models.CharField(
        "值类型", max_length=20, choices=ValueType.choices, default=ValueType.STRING
    )
    value = models.TextField("配置值", blank=True, default="")
    default_value = models.TextField("默认值", blank=True, default="")
    description = models.TextField("说明", blank=True, default="")
    client_visible = models.BooleanField("前端可读取", default=False)
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    options = models.JSONField("可选项/扩展配置", default=dict, blank=True)

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = "系统设置"
        constraints = [
            models.UniqueConstraint(
                fields=["namespace", "key"], name="uq_system_setting_key"
            ),
        ]
        indexes = [
            models.Index(
                fields=["namespace", "is_active", "sort_order"],
                name="idx_sysset_ns_active",
            ),
        ]
        ordering = ("namespace", "sort_order", "key")

    def __str__(self):
        return f"{self.namespace}.{self.key}"

    def effective_value(self):
        raw_value = self.value if self.value not in (None, "") else self.default_value
        if raw_value in (None, ""):
            return ""
        if self.value_type == self.ValueType.BOOLEAN:
            return str(raw_value).strip().lower() in {"1", "true", "yes", "on", "是"}
        if self.value_type == self.ValueType.INTEGER:
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                return 0
        if self.value_type == self.ValueType.JSON:
            try:
                return json.loads(raw_value)
            except (TypeError, ValueError):
                return {}
        return raw_value

    @classmethod
    def get_value(cls, namespace, key, default=None):
        setting = cls.objects.filter(
            namespace=namespace, key=key, is_active=True
        ).first()
        if not setting:
            return default
        value = setting.effective_value()
        return default if value in (None, "") else value


class PrintConfig(TimeStampedMixin):
    class Module(models.TextChoices):
        POS_SALE = "pos_sale", "POS销售单"
        POS_SHIFT = "pos_shift", "POS班次"
        OUTBOUND = "outbound", "出库单"
        GENERIC = "generic", "通用"

    class PrintMethod(models.TextChoices):
        FRONTEND_HTML = "frontend_html", "前端HTML打印"
        BACKEND_HTML = "backend_html", "后端打印页面"
        NATIVE = "native", "本机/原生打印"

    class PrinterType(models.TextChoices):
        LASER = "laser", "激光/喷墨"
        DOT_MATRIX = "dot_matrix", "针式"
        THERMAL = "thermal", "热敏"
        OTHER = "other", "其他"

    code = models.CharField("配置编码", max_length=80, unique=True)
    name = models.CharField("名称", max_length=100)
    module = models.CharField(
        "适用模块",
        max_length=30,
        choices=Module.choices,
        default=Module.POS_SALE,
        db_index=True,
    )
    print_method = models.CharField(
        "打印方式",
        max_length=30,
        choices=PrintMethod.choices,
        default=PrintMethod.FRONTEND_HTML,
    )
    printer_type = models.CharField(
        "打印机类型",
        max_length=30,
        choices=PrinterType.choices,
        default=PrinterType.LASER,
    )
    paper_mode = models.CharField("前端纸型标识", max_length=50, default="a4_landscape")
    paper_width = models.CharField("纸张宽度", max_length=30, default="A4")
    paper_height = models.CharField("纸张高度", max_length=30, default="landscape")
    page_size_css = models.CharField(
        "CSS纸张尺寸", max_length=80, default="A4 landscape"
    )
    page_margin = models.CharField("页面边距", max_length=30, default="0")
    sheet_width = models.CharField("单据宽度", max_length=30, default="98%")
    sheet_padding_top = models.CharField("上边距", max_length=30, default="1mm")
    sheet_padding_right = models.CharField("右内边距", max_length=30, default="0")
    sheet_padding_bottom = models.CharField("下内边距", max_length=30, default="0")
    sheet_padding_left = models.CharField("左内边距", max_length=30, default="0")
    font_family = models.CharField(
        "字体",
        max_length=200,
        default="Microsoft YaHei, Arial, sans-serif",
        help_text="使用逗号分隔字体名称，不需要引号。",
    )
    body_font_size = models.CharField("正文字号", max_length=30, default="22px")
    company_font_size = models.CharField(
        "公司/仓库名字号", max_length=30, default="36px"
    )
    title_font_size = models.CharField("标题字号", max_length=30, default="24px")
    meta_font_size = models.CharField("单据信息字号", max_length=30, default="22px")
    table_font_size = models.CharField("表格字号", max_length=30, default="22px")
    table_header_font_size = models.CharField("表头字号", max_length=30, default="22px")
    money_font_size = models.CharField("金额汇总字号", max_length=30, default="22px")
    footer_font_size = models.CharField("底部信息字号", max_length=30, default="22px")
    body_line_height = models.CharField("正文行高", max_length=20, default="1.15")
    meta_line_height = models.CharField("单据信息行高", max_length=20, default="1.15")
    table_line_height = models.CharField("表格行高", max_length=20, default="1.05")
    money_line_height = models.CharField("金额汇总行高", max_length=20, default="1.15")
    footer_line_height = models.CharField("底部信息行高", max_length=20, default="1.12")
    table_cell_padding = models.CharField(
        "表格单元格内边距", max_length=40, default="1px 2px"
    )
    money_gap = models.CharField("金额汇总列间距", max_length=30, default="6px")
    money_margin_top = models.CharField("金额汇总上间距", max_length=30, default="5px")
    extra = models.JSONField("扩展配置", default=dict, blank=True)
    is_default = models.BooleanField("默认", default=False)
    is_active = models.BooleanField("启用", default=True)
    sort_order = models.PositiveIntegerField("排序", default=0)
    remark = models.CharField("备注", max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "打印配置"
        verbose_name_plural = "打印配置"
        ordering = ("module", "sort_order", "code")
        indexes = [
            models.Index(
                fields=["module", "is_active", "is_default", "sort_order"],
                name="idx_printcfg_module",
            ),
        ]

    def __str__(self):
        return f"{self.name}({self.code})"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.is_default:
                type(self).objects.filter(
                    module=self.module,
                    is_default=True,
                ).exclude(
                    pk=self.pk
                ).update(is_default=False)

    @property
    def effective_page_size_css(self):
        if self.page_size_css:
            return self.page_size_css
        return f"{self.paper_width} {self.paper_height}".strip()

    @classmethod
    def get_default(cls, module, code=None):
        queryset = cls.objects.filter(module=module, is_active=True)
        if code:
            config = queryset.filter(code=code).first()
            if config:
                return config
        return (
            queryset.filter(is_default=True).first()
            or queryset.order_by("sort_order", "code").first()
        )


class AddressMixin(models.Model):
    province = models.CharField("省", max_length=30, blank=True, null=True)
    city = models.CharField("市", max_length=30, blank=True, null=True)
    district = models.CharField("区", max_length=30, blank=True, null=True)
    street = models.CharField("路/街道", max_length=80, blank=True, null=True)
    address = models.CharField("详细地址", max_length=200, blank=True, null=True)
    postal_code = models.CharField("邮编", max_length=10, blank=True, null=True)
    # 使用GeoDjango来存储地理坐标
    # location = geomodels.PointField(null=True, blank=True)

    class Meta:
        abstract = True


# ===============生成单据号#===============
class DocSequenceManager(models.Manager):
    def reserve(self, *, doc_type, biz_date, warehouse, owner) -> int:
        """
        原子地获取并递增一个序号，只返回整数。
        号段作用域: (doc_type, biz_date, warehouse, owner)
        """
        with transaction.atomic():
            try:
                row = self.select_for_update().get(
                    doc_type=doc_type,
                    biz_date=biz_date,
                    warehouse=warehouse,
                    owner=owner,
                )
            except DocSequence.DoesNotExist:
                # 并发下可能同时创建，撞唯一约束就回读锁行
                try:
                    # The savepoint keeps the outer transaction usable when
                    # another request wins the first-row insert race.
                    with transaction.atomic():
                        row = self.create(
                            doc_type=doc_type,
                            biz_date=biz_date,
                            warehouse=warehouse,
                            owner=owner,
                            next_no=1,
                        )
                except IntegrityError:
                    row = self.select_for_update().get(
                        doc_type=doc_type,
                        biz_date=biz_date,
                        warehouse=warehouse,
                        owner=owner,
                    )
            n = row.next_no
            row.next_no = n + 1
            row.save(update_fields=["next_no"])
            return n


class DocSequence(models.Model):
    """
    单据序列表：按 doc_type + biz_date + Wwarehouse + owner 划分号段
    """

    # —— 默认展示策略（放类属性，便于其他 app 统一引用d/覆盖）——
    DEFAULT_WIDTH: int = 5
    DEFAULT_FMT: str = "{prefix}-{yyyy}{mm}{dd}-{wh}-{own}-{seq}"

    # —— 号段关键字段 ——
    doc_type = models.CharField(max_length=16)  # 例如: 'INB','RCV','OUT',...
    biz_date = models.DateField()
    warehouse = models.ForeignKey(
        "locations.Warehouse", on_delete=models.PROTECT, null=True, blank=True
    )
    owner = models.ForeignKey(
        "baseinfo.Owner", on_delete=models.PROTECT, null=True, blank=True
    )
    next_no = models.BigIntegerField(default=1)  # 下一个可用序号(从1开始)

    objects = DocSequenceManager()

    class Meta:
        db_table = "doc_sequence"
        # 名称 ≤ 30 字符（你的项目规则）
        constraints = [
            models.UniqueConstraint(
                fields=["doc_type", "biz_date", "warehouse", "owner"],
                name="uq_docseq_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=["doc_type", "biz_date", "warehouse", "owner"],
                name="ix_docseq_scope",
            ),
        ]
        verbose_name = "单据序列"
        verbose_name_plural = "单据序列"

    def __str__(self):
        return f"{self.doc_type}-{self.biz_date} [{self.warehouse_id}/{self.owner_id}] next={self.next_no}"

    # —— 类方法：对外提供数字 & 成品单号 —— #
    @classmethod
    def next_number(cls, *, doc_type: str, warehouse, owner, biz_date=None) -> int:
        """
        返回下一个序号（int），不关心格式。
        """
        biz_date = biz_date or date.today()
        return cls.objects.reserve(
            doc_type=doc_type, biz_date=biz_date, warehouse=warehouse, owner=owner
        )

    @classmethod
    def next_code(
        cls,
        *,
        doc_type: str,
        warehouse,
        owner,
        biz_date=None,
        width: int | None = None,
        fmt: str | None = None,
        wh_get=lambda w: getattr(w, "code", None) or str(getattr(w, "id", "")),
        own_get=lambda o: getattr(o, "code", None) or str(getattr(o, "id", "")),
    ) -> str:
        """
        返回格式化后的单号字符串（零填充）。
        例: INB-20250829-WH01-OWN01-00001
        """
        biz_date = biz_date or date.today()
        n = cls.next_number(
            doc_type=doc_type, warehouse=warehouse, owner=owner, biz_date=biz_date
        )

        width = cls.DEFAULT_WIDTH if width is None else width
        fmt = cls.DEFAULT_FMT if fmt is None else fmt

        yyyy = f"{biz_date:%y}"
        mm = f"{biz_date:%m}"
        dd = f"{biz_date:%d}"
        prefix = doc_type.upper()
        wh = wh_get(warehouse)
        own = own_get(owner)
        seq_str = f"{n:0{width}d}"  # 固定位数，零填充

        return fmt.format(
            prefix=prefix, yyyy=yyyy, mm=mm, dd=dd, wh=wh, own=own, seq=seq_str
        )


# =============================================================


class IdempotentRequestMixin(models.Model):
    request_id = models.CharField(
        max_length=64, blank=False, null=False, db_index=True, unique=True
    )

    class Meta:
        abstract = True
