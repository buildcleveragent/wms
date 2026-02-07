# core/models/base.py
from django.conf import settings
from django.db import models, transaction, IntegrityError
from django.utils import timezone
from datetime import date



class _SkipCleanFlag:
    """简易的线程内标记；避免全局共享（够用且不引入依赖）。"""
    _local = {}

    @classmethod
    def get(cls, key): return cls._local.get(key, False)
    @classmethod
    def set(cls, key, val): cls._local[key] = bool(val)

class SoftDeleteMixin(models.Model):
    is_deleted = models.BooleanField("已删除", default=False)
    deleted_at = models.DateTimeField("删除时间", blank=True, null=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="删除人", on_delete=models.PROTECT, blank=True, null=True, related_name="%(class)s_deleted")

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
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="创建人", on_delete=models.PROTECT, blank=True, null=True, related_name="%(class)s_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="更新人", on_delete=models.PROTECT, blank=True, null=True, related_name="%(class)s_updated")

    class Meta:
        abstract = True

class BaseModel(UserStampedMixin, TimeStampedMixin, SoftDeleteMixin):
    is_active = models.BooleanField("启用状态", default=True)
    remark = models.CharField("备注", max_length=200, blank=True, null=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

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

#===============生成单据号#===============
class DocSequenceManager(models.Manager):
    def reserve(self, *, doc_type, biz_date, warehouse, owner) -> int:
        """
        原子地获取并递增一个序号，只返回整数。
        号段作用域: (doc_type, biz_date, warehouse, owner)
        """
        with transaction.atomic():
            try:
                row = (self.select_for_update()
                           .get(doc_type=doc_type, biz_date=biz_date,
                                warehouse=warehouse, owner=owner))
            except DocSequence.DoesNotExist:
                # 并发下可能同时创建，撞唯一约束就回读锁行
                try:
                    row = self.create(
                        doc_type=doc_type, biz_date=biz_date,
                        warehouse=warehouse, owner=owner, next_no=1
                    )
                except IntegrityError:
                    row = (self.select_for_update()
                               .get(doc_type=doc_type, biz_date=biz_date,
                                    warehouse=warehouse, owner=owner))
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
    doc_type  = models.CharField(max_length=16)   # 例如: 'INB','RCV','OUT',...
    biz_date  = models.DateField()
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, null=True, blank=True,editable=False,default=settings.DEFAULT_WAREHOUSE_ID)
    owner     = models.ForeignKey("baseinfo.Owner",       on_delete=models.PROTECT, null=True, blank=True)
    next_no   = models.BigIntegerField(default=1)    # 下一个可用序号(从1开始)

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
        cls, *,
        doc_type: str, warehouse, owner, biz_date=None,
        width: int | None = None, fmt: str | None = None,
        wh_get=lambda w: getattr(w, "code", None) or str(getattr(w, "id", "")),
        own_get=lambda o: getattr(o, "code", None) or str(getattr(o, "id", "")),
    ) -> str:
        """
        返回格式化后的单号字符串（零填充）。
        例: INB-20250829-WH01-OWN01-00001
        """
        biz_date = biz_date or date.today()
        n = cls.next_number(doc_type=doc_type, warehouse=warehouse, owner=owner, biz_date=biz_date)

        width = cls.DEFAULT_WIDTH if width is None else width
        fmt   = cls.DEFAULT_FMT  if fmt   is None else fmt

        yyyy = f"{biz_date:%y}"
        mm   = f"{biz_date:%m}"
        dd   = f"{biz_date:%d}"
        prefix = doc_type.upper()
        wh  = wh_get(warehouse)
        own = own_get(owner)
        seq_str = f"{n:0{width}d}"   # 固定位数，零填充

        return fmt.format(prefix=prefix, yyyy=yyyy, mm=mm, dd=dd, wh=wh, own=own, seq=seq_str)
#=============================================================

class IdempotentRequestMixin(models.Model):
    request_id = models.CharField(max_length=64, blank=False, null=False, db_index=True, unique=True)

    class Meta:
        abstract = True

