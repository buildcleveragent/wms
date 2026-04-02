# allapp/tasking/models.py
from django.template.context_processors import request

from allapp.tasking.utils import get_task_status_via_line
from django.db.models import F, Q, ExpressionWrapper, DecimalField
from django.contrib.contenttypes.models import ContentType
from datetime import datetime
from allapp.core.models import BaseModel, TimeStampedMixin, UserStampedMixin, DocSequence
from django.utils import timezone
from django.db.models import Q, F, Case, When, Value, IntegerField, ExpressionWrapper
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models, transaction
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from allapp.inventory.models import PostingJournal
from allapp.products.models import ProductUom

DEC_QTY = dict(max_digits=18, decimal_places=4)

def _norm(s):
    return (s or "").strip().upper() or None

class WmsTask(BaseModel):
    """任务头（统一承载：类型/状态/优先级/计划时间/来源快照/组号等）"""

    class TaskType(models.TextChoices):
        RECEIVE   = "RECEIVE",   "收货"
        PUTAWAY   = "PUTAWAY",   "上架"
        PICK      = "PICK",      "拣货"
        REVIEW    = "REVIEW",    "复核"
        PACK      = "PACK",      "打包"
        LOAD      = "LOAD",      "装车"
        DISPATCH  = "DISPATCH",  "发运"
        REPLEN    = "REPLEN",    "补货"
        RELOC     = "RELOC",     "移位"
        COUNT     = "COUNT",     "盘点"
        OTHER     = "OTHER",     "其他"
        ADJUST    = "ADJUST",    "调整"

    class Status(models.TextChoices):
        RESERVED    = "RESERVED",    "冻结预订"
        DRAFT       = "DRAFT",       "草稿"
        READY       = "READY",       "待发布"
        RELEASED    = "RELEASED",    "已发布"
        IN_PROGRESS = "IN_PROGRESS", "执行中"
        COMPLETED   = "COMPLETED",   "已完成"
        CANCELLED   = "CANCELLED",   "已取消"

    class Priority(models.IntegerChoices):
        LOW  = 1, "低"
        MED  = 2, "中"
        HIGH = 3, "高"

    class ReviewStatus(models.TextChoices):
        NONE = "NONE", "无需审核"
        NOT_READY = "NOT_READY", "未到审核时机"
        PENDING = "PENDING", "待审核"
        APPROVED = "APPROVED", "审核通过"
        REJECTED = "REJECTED", "已驳回"
        NEED_RECOUNT = "NEED_RECOUNT", "需复盘"

    class PostingStatus(models.TextChoices):
        NONE = "NONE", "无需过账"
        NOT_READY = "NOT_READY", "未到过账时机"
        PENDING = "PENDING", "待过账"
        POSTED = "POSTED", "已过账"
        FAILED = "FAILED", "过账失败"
        NEED_RECOUNT = "NEED_RECOUNT", "需复盘"

    # 审核
    review_status = models.CharField(verbose_name=_("审核状态"),max_length=50,  choices=ReviewStatus.choices,default=ReviewStatus.NOT_READY, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("审核人"), null=True, blank=True,on_delete=models.SET_NULL, related_name="tasks_approved")
    approved_at = models.DateTimeField(verbose_name=_("审核时间"), null=True, blank=True)
    approval_note = models.CharField(verbose_name=_("审核备注"),max_length=200,  blank=True, default="")

    picked_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("拣货人"), null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="tasks_picked_by")

    # 过账
    posting_status = models.CharField(verbose_name=_("过账状态"),max_length=50, choices=PostingStatus.choices,default=PostingStatus.NOT_READY, db_index=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("过账经手人"),null=True, blank=True,on_delete=models.SET_NULL, related_name="tasks_posted")
    posted_at = models.DateTimeField(verbose_name=_("过账时间"),null=True, blank=True)
    posting_note = models.CharField(verbose_name=_("过账备注"),max_length=200, blank=True, null=True,default="")

    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT,related_name="tasks", verbose_name=_("货主"))
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT,related_name="tasks", verbose_name=_("仓库"))

    task_group_no = models.CharField(_("计划组号"), max_length=40, blank=True, default="", db_index=True)
    released_at = models.DateTimeField(_("发布时间"), null=True, blank=True)

    task_no = models.CharField(_("任务号"), max_length=100, unique=True)
    task_type = models.CharField(_("任务类型"), max_length=12, choices=TaskType.choices)
    status = models.CharField(_("任务状态"), max_length=16, choices=Status.choices,db_index=True, default=Status.DRAFT)
    priority = models.PositiveSmallIntegerField(_("优先级"), choices=Priority.choices, default=Priority.MED)

    planned_start = models.DateTimeField(_("计划开始时间"), null=True, blank=True)
    planned_end   = models.DateTimeField(_("计划结束时间"), null=True, blank=True)
    started_at    = models.DateTimeField(_("实际开始时间"), null=True, blank=True)
    finished_at   = models.DateTimeField(_("实际完成时间"), null=True, blank=True)

    ref_no = models.CharField(_("来源单号"), max_length=60, blank=True, default="")
    source_app = models.CharField(_("来源应用"), max_length=40, blank=True, default="")
    source_model = models.CharField(_("来源模型"), max_length=40, blank=True, default="")
    source_pk = models.CharField(_("来源主键"), max_length=40, blank=True, default="")

    remark = models.CharField(_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "任务"
        verbose_name_plural = "任务"
        ordering = ["-created_at", "-id"]
        permissions = [
            ("taskconfirm_as_wh_manager",  _("仓管任务发布权")),
            ("claim_task_as_wh_operator", "仓库操作权"),
        ]
        constraints = [
            # 计划时间先后
            models.CheckConstraint(
                check=Q(planned_end__isnull=True) | Q(planned_start__isnull=True) | Q(planned_end__gte=F("planned_start")),
                name="chk_task_plan_ok",
            ),
            # 实际时间先后（可选）
            models.CheckConstraint(
                check=Q(finished_at__isnull=True) | Q(started_at__isnull=True) | Q(finished_at__gte=F("started_at")),
                name="chk_task_actual_ok",
            ),
            # 发布时间与状态一致性（避免 Meta 里引用 Status 枚举，直接用字符串）
            models.CheckConstraint(
                check=Q(released_at__isnull=True) |
                      Q(status__in=["RELEASED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]),
                name="chk_task_release_status_ok",
            ),
            models.CheckConstraint(
                name="ck_task_type_valid",
                check=Q(
                    task_type__in=["RECEIVE", "PUTAWAY", "PICK", "REVIEW","PACK", "LOAD", "DISPATCH", "REPLEN", "RELOC", "COUNT",
                                   "OTHER","ADJUST"]),
            ),
            models.CheckConstraint(
                name="ck_task_status_valid",
                check=Q(status__in=["RESERVED","DRAFT", "READY", "RELEASED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]),
            ),
            # 只有 COMPLETED 才允许 APPROVED/REJECTED/POSTED
            models.CheckConstraint(
                name="ck_task_review_after_completed",
                check=(
                        (~Q(status="COMPLETED") & Q(review_status__in=["NONE", "NOT_READY"]))
                        |
                        (Q(status="COMPLETED") & Q(review_status__in=["PENDING", "APPROVED", "REJECTED","NEED_RECOUNT"]))
                ),
                            ),

            models.CheckConstraint(
                name="ck_task_posting_after_approved",
                check=(
                        (~Q(review_status="APPROVED") & Q(posting_status__in=["NONE", "NOT_READY","NEED_RECOUNT"]))
                        |
                        (Q(review_status="APPROVED") & Q(posting_status__in=["PENDING", "POSTED", "FAILED"]))
                ),
            ),
        ]

        indexes = [
            models.Index(fields=["owner", "warehouse", "task_type", "status"], name="ix_task_wh_tt_st"),
            models.Index(fields=["owner", "warehouse", "status"], name="idx_task_wh_st"),
            models.Index(fields=["task_type", "status"], name="idx_task_tt_st"),
            models.Index(fields=["task_group_no", "status"], name="ix_task_grp_st"),
            # 如果确实需要结合 id 的覆盖/排序再加下面这一条；否则可以去掉
            # models.Index(fields=["owner", "warehouse", "task_type", "status", "id"], name="ix_tsk_wh_tt_st_id"),
        ]

    def __str__(self):
        return f"{self.task_no}({self.task_type})-{self.status}"

    def clean(self):
        super().clean()
        if not self.warehouse_id:
            raise ValidationError({"warehouse": _("必须明确指定任务仓库。")})

    def save(self, *args, **kwargs):
        if not self.warehouse_id:
            raise ValidationError({"warehouse": _("必须明确指定任务仓库。")})
        self.full_clean()
        return super().save(*args, **kwargs)

    def release(self, *, by_user=None):
        """
        发布任务：把当前任务从 DRAFT/READY 发布为 RELEASED。
        规则：
        - 必须有任务行，且至少一行计划数量 > 0
        - 仅允许从 DRAFT 或 READY 进入 RELEASED
        - 对“收货任务”也适用（如需限定类型，放开下面的判断）
        """
        # 如果只允许收货任务发布，取消注释：
        # if self.task_type != self.TaskType.RECEIVE:
        #     raise ValidationError("仅支持收货任务发布。")

        if self.status not in (self.Status.DRAFT, getattr(self.Status, "READY", self.Status.DRAFT)):
            raise ValidationError(f"任务状态为 {self.status}，仅允许在 DRAFT/READY 发布。")

        if not self.lines.exists():
            raise ValidationError("任务无明细，不能发布。")
        if not self.lines.filter(qty_plan__gt=0).exists():
            raise ValidationError("所有任务行计划数量为 0，不能发布。")

        # 通过校验，执行发布
        old = self.status
        self._allow_status_write = True  # 如你对 status 有 clean() 防护
        self.status = self.Status.RELEASED
        if hasattr(self, "released_at"):
            self.released_at = timezone.now()
        self.save(update_fields=["status"] + (["released_at"] if hasattr(self, "released_at") else []))

        # 可选：写状态日志（没有该表就忽略）
        try:
             TaskStatusLog.objects.create(task=self, old_status=old, new_status=self.status, changed_by=by_user)
        except Exception:
            pass

        return self

class WmsTaskLine(BaseModel):
    """任务行（明细）——承载商品、库位、数量与来源信息"""
    class Status(models.TextChoices):
        RESERVED    = "RESERVED", "冻结预订"
        DRAFT       = "DRAFT",       "草稿"
        READY       = "READY",       "待发布"
        RELEASED    = "RELEASED",    "已发布"
        IN_PROGRESS = "IN_PROGRESS", "执行中"
        COMPLETED   = "COMPLETED",   "已完成"
        CANCELLED   = "CANCELLED",   "已取消"

    task = models.ForeignKey("tasking.WmsTask", on_delete=models.PROTECT, related_name="lines", verbose_name=_("任务"),)
    product = models.ForeignKey("products.Product",on_delete=models.PROTECT,null=True, blank=True,verbose_name=_("商品"), )

    from_location = models.ForeignKey("locations.Location",on_delete=models.PROTECT,null=True,blank=True,related_name="from_lines",verbose_name=_("来源库位"),)
    to_location = models.ForeignKey("locations.Location",on_delete=models.PROTECT, null=True,blank=True,related_name="to_lines",verbose_name=_("去向库位"), )

    qty_plan = models.DecimalField(_("计划数量"),max_digits=18,decimal_places=3,default=0,)
    qty_done = models.DecimalField(_("已执行数量"),max_digits=18, decimal_places=3,default=0,)
    status = models.CharField(_("状态"), max_length=16, choices=Status.choices, db_index=True, default=Status.DRAFT)
    # 或者不用枚举，也可用时间戳表达完成：
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    remark = models.CharField(_("备注"),max_length=200, blank=True,  default="",    )

    # 业务来源（可选；与 bound_* 语义区分开）
    src_model = models.CharField(_("来源模型"), max_length=40, blank=True,default="",help_text=_("记录来源单据的模型名称（如 inbound.InboundOrderLine ）"), )
    src_id = models.BigIntegerField(_("来源ID"), null=True, blank=True, help_text=_("记录来源单据行或记录的主键ID"), )

    rule_key = models.CharField(_("规则键"),max_length=50,blank=True, default="", help_text=_("用于记录分配/策略命中的规则标识"), )
    plan_meta = models.JSONField(_("计划元数据"),default=dict, blank=True, help_text=_("用于保存与计划相关的额外字段（JSON）"), )

    # 绑定执行对象（如容器/托盘/拣货箱等）
    bound_content_type = models.ForeignKey(ContentType,on_delete=models.PROTECT,null=True,blank=True,verbose_name=_("绑定对象类型"), )
    bound_object_id = models.BigIntegerField(_("绑定对象ID"),null=True,blank=True,)
    bound_object = GenericForeignKey("bound_content_type", "bound_object_id")

    scan_snapshot_rev = models.IntegerField("收货快照版本",db_index=True,default=0,
        help_text="该任务行的收货事实快照版本号；每次保存重建快照时自增，用于生成/区分 TaskScanLog。")

    class Meta:
        verbose_name = _("任务行")
        verbose_name_plural = _("任务行")
        constraints = [
            models.CheckConstraint(check=Q(qty_plan__gte=0) & Q(qty_done__gte=0),name="ck_tl_qty_ge0",  ),
            models.CheckConstraint(name="ck_tline_snaprev_nonneg", check=Q(scan_snapshot_rev__gte=0), ),
        ]
        indexes = [
            models.Index(fields=["task", "product"], name="ix_tl_task_prod"),
            models.Index(fields=["from_location"], name="ix_tl_from_loc"),
            models.Index(fields=["to_location"], name="ix_tl_to_loc"),
            models.Index(fields=["bound_content_type", "bound_object_id"], name="ix_tl_bound_ct_oid"),
            models.Index(fields=["src_model", "src_id"], name="ix_tl_src_model_id"),
            # models.Index(fields=["scan_snapshot_rev"], name="idx_tline_snaprev"),
        ]

    def __str__(self):
        return f"TL@{self.task_id}#{self.id} plan={self.qty_plan} done={self.qty_done}"

    @property
    def is_finished(self):
        # return bool(self.finished_at) or self.status == self.Status.DONE
        return bool(self.finished_at) or self.status in (self.Status.COMPLETED, self.Status.CANCELLED)

    @property
    def qty_pending(self):
        return (self.qty_plan or 0) - (self.qty_done or 0)

    def clean(self):
        errs = {}

        wh_id = getattr(self.task, "warehouse_id", None)
        for loc in (self.from_location, self.to_location):
            if loc and wh_id and loc.warehouse_id != wh_id:
                raise ValidationError({"__all__": _("任务行库位必须与任务仓库一致")})

        # 1) 基本数量逻辑（如不允许超量，打开下面一行）
        # if self.qty_plan is not None and self.qty_done is not None and self.qty_done > self.qty_plan:
        #     errs["qty_done"] = _("已执行数量不可超过计划数量。")

        # 2) 仓库一致性（跨表校验）
        if self.task_id:
            tw = getattr(self.task, "warehouse_id", None)
            if self.from_location_id and self.from_location.warehouse_id != tw:
                errs["from_location"] = _("来源库位不属于任务所在仓库。")
            if self.to_location_id and self.to_location.warehouse_id != tw:
                errs["to_location"] = _("去向库位不属于任务所在仓库。")
            if self.from_location_id and self.to_location_id and self.from_location_id == self.to_location_id:
                errs["to_location"] = _("来源与去向库位不能相同。")

            # 3) 位置形态与任务类型（示例；可按需调整/放服务层）
            t = getattr(self.task, "task_type", None)
            if t == "RELOC":
                if not (self.from_location_id and self.to_location_id):
                    errs["from_location"] = _("移位任务行必须同时指定来源与去向库位。")
            elif t == "PICK":
                if not self.from_location_id:
                    errs["from_location"] = _("拣货任务行必须指定来源库位。")
            elif t == "PUTAWAY":
                if not self.to_location_id:
                    errs["to_location"] = _("上架任务行必须指定去向库位。")
            # 其它类型按你的规则再补

        if errs:
            raise ValidationError(errs)

class TaskAssignment(TimeStampedMixin):
    """任务领用/指派（同一任务与同一人唯一）"""

    task = models.ForeignKey("tasking.WmsTask",on_delete=models.PROTECT, related_name="assignments", verbose_name=_("任务"), )
    line = models.ForeignKey("tasking.WmsTaskLine", null=True, blank=True, on_delete=models.PROTECT, related_name="assignments", verbose_name=_("任务明细"),)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name="task_assignments",verbose_name=_("作业员"),)

    accepted_at = models.DateTimeField(_("领取时间"), null=True, blank=True)
    finished_at = models.DateTimeField(_("完成时间"), null=True, blank=True)

    def clean(self):
        # 保障“行属于任务”
        if self.line_id and self.line.task_id != self.task_id:
            from django.core.exceptions import ValidationError
            raise ValidationError({"line": "所选行不属于当前任务。"})

    class Meta:
        verbose_name = "任务指派"
        verbose_name_plural = "任务指派"
        constraints = [
            # 同一 task/assignee/line 唯一（历史可多条，活动状态靠 finished_at 控制）,但不支持1人认领多次
            # models.UniqueConstraint(fields=["task", "assignee", "line"], name="ux_assign_task_user_line"),
            # 时间一致性
            models.CheckConstraint(
                name="ck_assign_time_flow",
                check=Q(finished_at__isnull=True) |
                      (Q(accepted_at__isnull=False) & Q(finished_at__gte=F("accepted_at"))),
            ),
        ]
        indexes = [
            # 行 → 活动指派（抢单/认领、放回都命中）
            models.Index(fields=["line", "finished_at"], name="idx_assign_line_active"),

            # 任务头 → 活动头级指派（判断是否允许抢单头、发布为抢单等）
            models.Index(fields=["task", "finished_at", "line"], name="idx_assign_task_head_active"),

            # 人 → 活动指派（“我的任务/行”视图）
            models.Index(fields=["assignee", "finished_at"], name="idx_assign_user_active"),

            # 报表/排序常用（谁先领，用于时间排序）
            models.Index(fields=["assignee", "accepted_at"], name="idx_assign_user_accept"),
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{getattr(self.task, 'task_no', self.task_id)} -> {self.assignee_id}"

class TaskStatusLog(models.Model):
    """任务状态变更审计"""

    # 若不在同一 app，请改为 "tasking.WmsTask"
    task = models.ForeignKey(
        "tasking.WmsTask",
        on_delete=models.PROTECT,
        related_name="status_logs",
        verbose_name=_("任务"),
    )

    # 与 WmsTask.status 统一长度；并限制 choices（更规范）
    old_status = models.CharField(_("旧状态"), max_length=16, choices=WmsTask.Status.choices)
    new_status = models.CharField(_("新状态"), max_length=16, choices=WmsTask.Status.choices)

    # 审计人：SET_NULL 更符合日志语义
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name=_("变更人"),
        related_name="task_status_changes",
    )
    changed_at = models.DateTimeField(_("变更时间"), auto_now_add=True)
    note = models.CharField(_("备注"), max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "任务状态日志"
        verbose_name_plural = "任务状态日志"
        ordering = ["-changed_at", "-id"]
        indexes = [
            models.Index(fields=["task", "changed_at"], name="idx_tlog_task_time"),
            models.Index(fields=["task", "new_status"], name="idx_tlog_task_newst"),   # 常见过滤
            models.Index(fields=["changed_by"], name="idx_tlog_by"),                    # 按人查（可选）
        ]
        constraints = [
            # 旧状态 != 新状态
            models.CheckConstraint(
                name="ck_tlog_old_ne_new",
                check=~Q(old_status=models.F("new_status")),
            ),
            # 兜底：两边都必须是合法取值（与 choices 保持一致，使用字面量避免内嵌类作用域问题）
            models.CheckConstraint(
                name="ck_tlog_old_in_set",
                check=Q(old_status__in=["DRAFT","READY","RELEASED","IN_PROGRESS","COMPLETED","CANCELLED"]),
            ),
            models.CheckConstraint(
                name="ck_tlog_new_in_set",
                check=Q(new_status__in=["DRAFT","READY","RELEASED","IN_PROGRESS","COMPLETED","CANCELLED"]),
            ),
        ]

    def __str__(self):
        return f"{getattr(self.task, 'task_no', self.task_id)}: {self.old_status} -> {self.new_status}"

class TaskScanLog(TimeStampedMixin):
    """任务扫描/WIP事实记录（幂等、可回放、可复核；过账成功时据此落账）"""
    # —— 扫描结果态 —— #
    class ScanStatus(models.TextChoices):
        OK = "OK", "成功"
        FAIL = "FAIL", "失败"
        DUP = "DUP", "重复"
        IGNORED = "IGNORED", "忽略"

    # —— 录入来源与操作者 —— #
    class Method(models.TextChoices):
        SCAN   = "SCAN",   "扫码"
        MANUAL = "MANUAL", "手工"
        API    = "API",    "系统导入"
        WEB    = "WEB",    "网页"

        # —— 复核流程 —— #
    class ReviewStatus(models.TextChoices):
        NONE = "NONE", "不需复核"
        PENDING = "PENDING", "待复核"
        APPROVED = "APPROVED", "已通过"
        REJECTED = "REJECTED", "已驳回"
    # —— 归属 —— #
    owner     = models.ForeignKey("baseinfo.Owner",on_delete=models.PROTECT,verbose_name="货主",db_index=True)
    warehouse = models.ForeignKey("locations.Warehouse",on_delete=models.PROTECT,verbose_name="仓库",db_index=True)
    task = models.ForeignKey("tasking.WmsTask", verbose_name=_("任务"),on_delete=models.PROTECT, related_name="scan_logs")
    task_line = models.ForeignKey("tasking.WmsTaskLine", verbose_name=_("任务行"),on_delete=models.PROTECT, null=True, blank=True, related_name="scan_logs")
    product = models.ForeignKey("products.Product", verbose_name=_("商品"),on_delete=models.PROTECT, null=True, blank=True)  # ✅ 允许未知SKU
    location = models.ForeignKey("locations.Location", verbose_name=_("库位"),on_delete=models.PROTECT, null=True, blank=True)

    # —— 扫码/标签 —— #
    barcode   = models.CharField(_("条码"), max_length=128, blank=True, null=True, db_index=True)
    label_key = models.CharField(_("箱标/序列唯一键"), max_length=64, blank=True, null=True)

    method = models.CharField(_("录入方式"), max_length=10, choices=Method.choices,default=Method.SCAN, db_index=True)
    source = models.CharField(_("来源终端"), max_length=10,choices=[("PDA", "PDA"), ("PC", "PC"), ("WEB", "WEB"), ("API", "API")],default="PDA")
    by_user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("操作人"),on_delete=models.SET_NULL, null=True, blank=True, related_name="task_scan_ops")
    device_id = models.CharField(_("设备ID"), max_length=64, blank=True, null=True)
    client_ts = models.DateTimeField(_("端上时间"), null=True, blank=True)


    # —— 解析快照 —— #
    code_type = models.CharField(_("码类型"), max_length=16, blank=True, null=True)   # UNIT/LPN/SSCC/GTIN...
    uom_code  = models.CharField(_("解析单位"), max_length=20, blank=True, null=True) # EA/CS/LPN...
    pack_qty  = models.DecimalField(_("换算(包→基本)"), max_digits=14, decimal_places=6, null=True, blank=True)

    # —— 数量（基本单位） —— #
    qty_aux        = models.DecimalField(_("包数(+/-)"), max_digits=18, decimal_places=3, null=True, blank=True)
    qty_base       = models.DecimalField(_("本次基本单位数(+/-)"), max_digits=18, decimal_places=6, null=True, blank=True)
    qty_base_delta = models.DecimalField(_("本次增量(基本单位)"), max_digits=18, decimal_places=6, null=True, blank=True)

    # —— 批次/日期/容器 —— #
    lot_no       = models.CharField(_("批号"), max_length=60, blank=True, null=True)
    mfg_date     = models.DateField(_("生产日期"), blank=True, null=True)
    exp_date     = models.DateField(_("有效期至"), blank=True, null=True)
    container_no = models.CharField(_("容器/托盘号"), max_length=40, blank=True, null=True)

    status = models.CharField(_("结果"), max_length=8, choices=ScanStatus.choices, default=ScanStatus.OK, db_index=True)
    void_reason=models.CharField(_("作废原因"), max_length=50, blank=True, null=True)
    error_code = models.CharField(_("错误码"), max_length=30, blank=True, null=True)
    error_msg  = models.CharField(_("错误信息"), max_length=200, blank=True, null=True)

    review_status = models.CharField(_("复核状态"), max_length=10,choices=ReviewStatus.choices, default=ReviewStatus.NONE, db_index=True)
    reason_code = models.CharField(_("原因代码"), max_length=20, blank=True, null=True)
    remark      = models.CharField(_("备注"), max_length=200, blank=True, null=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("复核人"),on_delete=models.SET_NULL, null=True, blank=True, related_name="task_scan_reviews")
    reviewed_at = models.DateTimeField(_("复核时间"), null=True, blank=True)

    # —— 过账关联 & 幂等 —— #
    posting_journal = models.ForeignKey("inventory.PostingJournal", verbose_name=_("过账日记账"),on_delete=models.SET_NULL, null=True, blank=True, related_name="scan_logs")
    fp = models.CharField(_("去重指纹"), max_length=64, db_index=True)
    scan_snapshot_rev = models.IntegerField("收货快照版本",db_index=True,
        help_text="该任务行的收货事实快照版本号；每次保存重建快照时自增，用于生成/区分 TaskScanLog。")

    posted_at = models.DateTimeField("过账时间", null=True, blank=True, db_index=True)
    posting_batch = models.CharField("过账批次", max_length=40, null=True, blank=True, db_index=True)
    # 可与 posting_journal 并存；posted_at/批次是“冗余打点”，查询更快

    class Meta:
        verbose_name = "任务扫描记录"
        verbose_name_plural = "任务扫描记录"
        ordering = ["-created_at", "-id"]
        constraints = [
            # 成功记录必须有非零增量；非成功可为空或0
            models.UniqueConstraint(fields=["fp"], name="ux_tscan_fp"),
            models.UniqueConstraint(fields=["task", "label_key"], name="ux_tscan_task_label"),

            # models.CheckConstraint(name="ck_tscan_ok_qty",check=Q(status="OK") & ~Q(qty_base_delta=0) | ~Q(status="OK"),),
            models.CheckConstraint(
                name="ck_tscan_ok_qty",
                check=(
                        ~Q(status="OK") |
                        (Q(qty_base_delta__isnull=False) & ~Q(qty_base_delta=0)) |
                        (Q(qty_base__isnull=False) & ~Q(qty_base=0))
                ),
            ),
            models.CheckConstraint(name="ck_tscan_pack_pos",check=Q(pack_qty__isnull=True) | Q(pack_qty__gt=0)),
            models.CheckConstraint(name="ck_tscan_method_ok",check=Q(method__in=["SCAN", "MANUAL", "API", "WEB"])),
            models.CheckConstraint(name="ck_tscan_review_ok",check=Q(review_status__in=["NONE", "PENDING", "APPROVED", "REJECTED"])),
            models.CheckConstraint(name="ck_tscan_status_ok",check=Q(status__in=["OK", "FAIL", "DUP", "IGNORED"])),
            models.CheckConstraint(name="ck_tl_snaprev_nonneg",check=Q(scan_snapshot_rev__gte=0)),
        ]
        indexes = [
            models.Index(fields=["task_line", "created_at"], name="ix_tscan_line_time"),
            models.Index(fields=["by_user", "created_at"], name="ix_tscan_user_time"),
            models.Index(fields=["task"], name="ix_tscan_task"),
            models.Index(fields=["product", "lot_no", "exp_date", "location"], name="ix_tscan_plxl"),
            models.Index(fields=["container_no"], name="ix_tscan_container"),
            # models.Index(fields=["scan_snapshot_rev"], name="idx_scanlog_snaprev2"),
        ]

    def __str__(self):
        return f"{self.task_id}-{self.barcode or self.label_key or ''}"

    def _sync_scope_from_relations(self):
        if self.task_id and not self.warehouse_id:
            self.warehouse_id = self.task.warehouse_id
        if not self.warehouse_id and self.location_id:
            self.warehouse_id = self.location.warehouse_id

    # —— 一致性与归一化 —— #
    def clean(self):
        self._sync_scope_from_relations()

        def _norm(s): return (s or "").strip()
        if isinstance(self.barcode, str):
            self.barcode = _norm(self.barcode) or None
        if isinstance(self.label_key, str):
            self.label_key = (_norm(self.label_key).upper()) or None
        if isinstance(self.lot_no, str):
            self.lot_no = (_norm(self.lot_no).upper()) or None
        if isinstance(self.uom_code, str):
            self.uom_code = (_norm(self.uom_code).upper()) or None
        if isinstance(self.code_type, str):
            self.code_type = (_norm(self.code_type).upper()) or None
        if isinstance(self.container_no, str):
            self.container_no = (_norm(self.container_no).upper()) or None

        errors = {}

        # 包装换算 → 基本数量一致性
        if self.pack_qty and self.qty_aux is not None:
            calc = (Decimal(self.qty_aux) * Decimal(self.pack_qty)).quantize(Decimal("0.000001"))
            if self.qty_base is None:
                self.qty_base = calc
            else:
                tol = max(Decimal("0.000001"), (calc * Decimal("0.001")))
                if (Decimal(self.qty_base) - calc).copy_abs() > tol:
                    errors["qty_base"] = _("基本数量与包数×换算不一致")

        # 扫码模式若强制需要条码，可在此开启
        # if self.method == self.Method.SCAN and not self.barcode:
        #     errors["barcode"] = _("扫码录入必须提供条码")

        if not self.warehouse_id:
            errors["warehouse"] = _("必须明确指定扫描记录仓库。")

        if self.task_id:
            if self.owner_id and self.owner_id != self.task.owner_id:
                errors["owner"] = _("扫描记录货主必须与任务货主一致。")
            if self.warehouse_id and self.warehouse_id != self.task.warehouse_id:
                errors["warehouse"] = _("扫描记录仓库必须与任务仓库一致。")

        if self.location_id and self.warehouse_id and self.location.warehouse_id != self.warehouse_id:
            errors["location"] = _("扫描记录库位必须属于当前仓库。")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._sync_scope_from_relations()
        self.full_clean()
        return super().save(*args, **kwargs)


class TaskExtraBase(models.Model):
    """
    任务头 Extra 抽象基类：
    - 与 WmsTask 一对一
    - 可在子类中冗余 owner/warehouse（若需要）
    - 校验任务类型匹配
    """
    task = models.OneToOneField(
        "tasking.WmsTask",                 # ✅ 用字符串路径更稳
        on_delete=models.PROTECT,
        related_name="%(class)s",
        verbose_name="任务",
    )

    class Meta:
        abstract = True

    @classmethod
    def expected_task_type(cls) -> str | None:  # 若 <3.10 改成 Optional[str]
        return None

    def clean(self):
        super().clean()
        if self.task_id:
            et = self.expected_task_type()
            if et and self.task.task_type != et:   # ✅ 修正字段名
                raise ValidationError(
                    f"任务头 Extra 类型({et})与任务类型({self.task.task_type})不匹配"
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
class TaskLineExtraBase(models.Model):
    """
    行级 Extra 抽象基类：
    - 与 WmsTaskLine 一对一
    - 校验行所属任务类型（可由子类覆盖 expected_task_type）
    """
    line = models.OneToOneField(
        "tasking.WmsTaskLine",             # ✅ 用字符串路径更稳
        on_delete=models.PROTECT,
        related_name="%(class)s",
        verbose_name="任务行",null=True, blank=True,
    )

    class Meta:
        abstract = True

    @classmethod
    def expected_task_type(cls) -> str | None:   # 若 <3.10 改成 Optional[str]
        return None

    def clean(self):
        super().clean()
        et = self.expected_task_type()
        if et and self.line_id and self.line.task.task_type != et:  # ✅ 修正字段名
            raise ValidationError(
                {"line": f"Extra 类型({et})与行任务类型({self.line.task.task_type})不匹配"}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

# ========= 各类型Extra 头部和行明细 =========
# ==收货
class ReceiveTaskExtra(TaskExtraBase):
    """收货头：预约/承运/供应商/收货模式等（行默认值与校验策略的来源）"""
    receive_mode = models.CharField("收货模式", max_length=10,choices=[("WITH_ASN", "按入库订单核对"), ("BLIND", "盲收")],default="WITH_ASN",    )
    appt_start = models.DateTimeField("预约开始", null=True, blank=True)  # ← 去掉 db_index
    appt_end   = models.DateTimeField("预约结束", null=True, blank=True)
    dock_code  = models.CharField("月台编码", max_length=20, blank=True, default="")
    vehicle_no = models.CharField("车牌号", max_length=20, blank=True, default="")   # ← 去掉 db_index
    qc_required = models.BooleanField("是否质检", default=False)

    class Meta:
        verbose_name = "收货任务头扩展"
        verbose_name_plural = "收货任务头扩展"
        # OneToOne 已唯一，这里不再加 UniqueConstraint
        indexes = [
            models.Index(fields=["appt_start"], name="ix_rcvtsk_appt"),
            models.Index(fields=["vehicle_no"], name="ix_rcvtsk_vno"),
        ]

    @classmethod
    def expected_task_type(cls):  # 用于基类校验
        return "RECEIVE"

    def clean(self):

        super().clean()
        if self.appt_start and self.appt_end and self.appt_end < self.appt_start:
            raise ValidationError({"appt_end": "预约结束时间不能早于开始时间"})
class ReceiveLineExtra(TaskLineExtraBase):
    """收货：批/效期 + 合格/破损/拒收 + 异常原因（行完成→PostingHandler 入账）"""
    from_lpn = models.CharField("上游容器号", max_length=40, null=True, blank=True, db_index=True)

    # 批/效期域
    lot_no   = models.CharField("批号", max_length=60, null=True, blank=True, db_index=True)
    mfg_date = models.DateField("生产日期", null=True, blank=True)
    exp_date = models.DateField("有效期至", null=True, blank=True)

    # 数量（建议在服务层同步到 WmsTaskLine.qty_done）
    qty_ok   = models.DecimalField("合格数",  max_digits=14, decimal_places=3, default=0)   # ← 与全局对齐为 3 位
    qty_damage = models.DecimalField("破损数",  max_digits=14, decimal_places=3, default=0)
    qty_reject = models.DecimalField("拒收数",  max_digits=14, decimal_places=3, default=0)

    # 异常原因（可选）
    damage_reason_code = models.CharField("破损原因", max_length=20, blank=True, default="")
    reject_reason_code = models.CharField("拒收原因", max_length=20, blank=True, default="")

    class Meta:
        verbose_name = "收货扩展"
        verbose_name_plural = "收货扩展"
        constraints = [
            # OneToOne 已唯一，不再额外加 UniqueConstraint(line)
            models.CheckConstraint(check=models.Q(qty_ok__gte=0),   name="ck_rcv_qd_ge0"),
            models.CheckConstraint(check=models.Q(qty_damage__gte=0), name="ck_rcv_qdmg_ge0"),
            models.CheckConstraint(check=models.Q(qty_reject__gte=0), name="ck_rcv_qrej_ge0"),
            # 至少有一个数量 > 0（可选，按业务需求）
            # models.CheckConstraint(
            #     name="ck_rcv_any_qty_pos",
            #     check=(models.Q(qty_done__gt=0) | models.Q(qty_damage__gt=0) | models.Q(qty_reject__gt=0))
            # ),
        ]
        indexes = [
            models.Index(fields=["lot_no"], name="ix_rcv_lot"),
            models.Index(fields=["exp_date"], name="ix_rcv_exp"),
            models.Index(fields=["from_lpn"], name="ix_rcv_flpn"),
            models.Index(fields=["lot_no", "exp_date"], name="ix_rcv_lot_exp"),
            models.Index(fields=["from_lpn", "lot_no"], name="ix_rcv_flpn_lot"),
        ]

    def __str__(self):
        # 只用 line_id，千万别去解引用 self.line（会在未赋值阶段触发异常）
        return f"ReceiveLineExtra(line_id={self.line_id or '-'})"

    def _get_task_status(self):
        """
        返回任务头(WmsTask)的 status。
        优先用已缓存的 self.line.task；若未缓存，用 task_id 做一次轻量查询。
        """
        line = getattr(self, "line", None)
        if not line:
            return None

        # 1) 若已预取（select_related）过，直接用缓存对象，不额外查库
        task = getattr(line, "task", None)
        if task is not None and getattr(task, "status", None) is not None:
            return task.status

        # 2) 未缓存则用 task_id 轻量取单列（避免加载整个对象）
        task_id = getattr(line, "task_id", None)
        if task_id:
            # from allapp.tasking.models import WmsTask  # 就地导入避免循环依赖
            return (WmsTask.objects
                    .filter(pk=task_id)
                    .values_list("status", flat=True)
                    .first())
        return None

    @classmethod
    def expected_task_type(cls):
        return "RECEIVE"

    def clean(self):

        super().clean()
        task_status = self._get_task_status()

        # 归一化
        def _norm(s):
            return (s or "").strip().upper() or None
        if isinstance(self.from_lpn, str): self.from_lpn = _norm(self.from_lpn)
        if isinstance(self.lot_no, str):  self.lot_no  = _norm(self.lot_no)

        # 日期先后
        if self.mfg_date and self.exp_date and self.exp_date < self.mfg_date:
            raise ValidationError({"exp_date": "有效期不得早于生产日期"})

        # 基于商品配置的校验
        # is_final = task_status in (WmsTask.Status.COMPLETED)
        is_final = (task_status or "") == WmsTask.Status.COMPLETED
        if is_final:
         p = getattr(self.line, "product", None)
         if p:
            if getattr(p, "batch_control", False):
                if not self.lot_no:
                    raise ValidationError({"lot_no": "该商品启用批次管理，必须录入批号"})
            # else:
            #     if self.lot_no:
            #         raise ValidationError({"lot_no": "该商品未启用批次管理，批号必须留空"})

            if getattr(p, "expiry_control", False):
                if not self.exp_date:
                    raise ValidationError({"exp_date": "该商品启用效期管理，必须录入有效期"})
            # else:
            #     if self.mfg_date or self.exp_date:
            #         raise ValidationError({"exp_date": "该商品未启用效期管理，生产/到期日必须留空"})

    def _calc_total_processed(self) -> Decimal:
        """按业务口径，行的完成进度 = 合格 + 破损 + 拒收"""
        z = Decimal("0")
        return (Decimal(self.qty_ok or 0)
                + Decimal(self.qty_damage or 0)
                + Decimal(self.qty_reject or 0)) or z

    @transaction.atomic
    def save(self, *args, **kwargs):
        # 1) 严格校验
        self.full_clean()

        # 2) 先保存扩展本身
        ret = super().save(*args, **kwargs)

        # 3) 同步到任务行进度（覆盖式重算）
        line = getattr(self, "line", None)
        if line_id := getattr(self, "line_id", None):
            total = self._calc_total_processed()

            # 行级并发保护（可选）
            (WmsTaskLine.objects
             .filter(pk=line_id)
             .update(qty_done=total))

            # 若当前实例里还要马上读取最新值，手动回填：
            if line is not None:
                line.qty_done = total

                # 达到/超过计划 → 触发行完成收尾
                line = getattr(self, "line", None)  # 可能已 select_related 了
                qty_plan = getattr(line, "qty_plan", None)
                if qty_plan is None:
                    # 无计划也可按“>0即完成”的口径触发；如不需要可去掉
                    should_finish = (total > 0)
                else:
                    should_finish = (total >= qty_plan)

                if should_finish:
                    # by_user 从线程本地/请求上下文取；Admin 场景下可在表单保存 hook 内注入
                    by_user = getattr(self, "_by_user", None)  # 可由 Admin formset 注入
                    from allapp.tasking.services import finalize_receive_line
                    try:
                        finalize_receive_line(self.line_id, by_user=by_user, trigger="AUTO_ON_REACH_PLAN")
                    except ValidationError:
                        # 不阻断保存；把错误交给上游 Admin 动作提示即可
                        pass

        return ret

    @transaction.atomic
    def delete(self, *args, **kwargs):
        # 一对一被删时，把行进度复位（你也可以改成保留原值或自定义规则）
        line_id = getattr(self, "line_id", None)
        ret = super().delete(*args, **kwargs)
        if line_id:
            from allapp.tasking.models import WmsTaskLine
            WmsTaskLine.objects.filter(pk=line_id).update(qty_done=0)
        return ret

# == 上架头 ==
class PutawayTaskExtra(TaskExtraBase):
    """上架头：策略/目标区域/是否可混放等"""
    strategy = models.CharField(
        "上架策略", max_length=10,
        choices=[("DIRECT", "直上"), ("RULE", "规则"), ("MANUAL", "人工")],
        default="RULE"
    )

    class Meta:
        verbose_name = "上架任务头扩展"
        verbose_name_plural = "上架任务头扩展"
        # OneToOne 本身唯一，无需再加 UniqueConstraint
        # 如后续增加高频过滤字段，再补索引

    @classmethod
    def expected_task_type(cls):
        return "PUTAWAY"
# == 上架行 ==
class PutawayLineExtra(TaskLineExtraBase):
    """上架：建议/目标库位、来源/目标容器号、移位数量（事实在行层）"""

    # 建议位（可选）
    plan_to_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="putaway_plan_targets",
        verbose_name="建议库位",
    )

    # 实际目标
    to_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="putaway_done_targets",
        verbose_name="目标库位",
    )

    # 容器（使用 NULL 表达“无值”，更易于唯一/索引语义）
    from_lpn = models.CharField("上游容器号", max_length=40, null=True, blank=True)
    to_lpn   = models.CharField("目标容器号", max_length=40, null=True, blank=True)

    # 数量：若你用 WmsTaskLine.qty_done 作唯一完成量，这里可删；保留则务必同步
    qty_moved = models.DecimalField("移入数", max_digits=14, decimal_places=3, default=0)

    class Meta:
        verbose_name = "上架扩展"
        verbose_name_plural = "上架扩展"
        constraints = [
            # OneToOne(line) 已唯一，不再重复
            models.CheckConstraint(check=models.Q(qty_moved__gte=0), name="ck_put_qmv_ge0"),
        ]
        indexes = [
            models.Index(fields=["plan_to_location"], name="ix_put_ploc"),
            models.Index(fields=["to_location"], name="ix_put_tloc"),
            models.Index(fields=["from_lpn"], name="ix_put_flpn"),
            models.Index(fields=["to_lpn"], name="ix_put_tlpn"),
            models.Index(fields=["to_location", "to_lpn"], name="ix_put_tloc_lpn"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "PUTAWAY"

    def clean(self):
        super().clean()

        # 归一化 LPN
        if isinstance(self.from_lpn, str):
            self.from_lpn = _norm(self.from_lpn)
        if isinstance(self.to_lpn, str):
            self.to_lpn = _norm(self.to_lpn)

        # 建议/目标库位若存在，必须与任务仓库一致
        if self.line_id:
            wh_id = self.line.task.warehouse_id
            if self.plan_to_location_id and self.plan_to_location.warehouse_id != wh_id:
                raise ValidationError({"plan_to_location": "建议库位不在本任务仓库"})
            if self.to_location_id and self.to_location.warehouse_id != wh_id:
                raise ValidationError({"to_location": "目标库位不在本任务仓库"})

        # 业务建议：有数量就要有目标库位
        if (self.qty_moved or 0) > 0 and not self.to_location_id:
            raise ValidationError({"to_location": "有移入数量时必须指定目标库位"})

    @transaction.atomic
    def save(self, *args, **kwargs):
        # 1) 严格校验
        self.full_clean()

        # 2) 先保存扩展本身
        ret = super().save(*args, **kwargs)
        total=getattr(self,'qty_moved',None)
        # 3) 同步到任务行进度（覆盖式重算）
        line = getattr(self, "line", None)
        if line_id := getattr(self, "line_id", None):
               # 行级并发保护（可选）
            (WmsTaskLine.objects
             .filter(pk=line_id)
             .update(qty_done=total))

            # 若当前实例里还要马上读取最新值，手动回填：
            if line is not None:
                line.qty_done = total

                # 达到/超过计划 → 触发行完成收尾
                line = getattr(self, "line", None)  # 可能已 select_related 了
                qty_plan = getattr(line, "qty_plan", None)
                if qty_plan is None:
                    # 无计划也可按“>0即完成”的口径触发；如不需要可去掉
                    should_finish = (total > 0)
                else:
                    should_finish = (total >= qty_plan)

                if should_finish:
                    # by_user 从线程本地/请求上下文取；Admin 场景下可在表单保存 hook 内注入
                    by_user = getattr(self, "_by_user", None)  # 可由 Admin formset 注入
                    from allapp.tasking.services import finalize_receive_line
                    try:
                        from allapp.tasking.services import finalize_task_line
                        finalize_task_line(self.line_id, by_user=by_user, trigger="AUTO_ON_REACH_PLAN")
                    except ValidationError:
                        # 不阻断保存；把错误交给上游 Admin 动作提示即可
                        pass

        return ret

# ==拣货
class PickTaskExtra(TaskExtraBase):
    wave_no = models.CharField("波次号", max_length=30, blank=True, default="")   # 去掉 db_index
    pick_mode = models.CharField(
        "拣货模式", max_length=10,
        choices=[("SINGLE","单单"),("BATCH","批量"),("CLUSTER","多单汇拣")],
        default="SINGLE"
    )
    route_code = models.CharField("拣货路径", max_length=20, blank=True, default="")
    ship_date  = models.DateField("计划出库日", null=True, blank=True)           # 去掉 db_index
    cutoff_at  = models.DateTimeField("截单时间", null=True, blank=True)

    class Meta:
        verbose_name = "拣货任务头扩展"
        verbose_name_plural = "拣货任务头扩展"
        indexes = [
            models.Index(fields=["wave_no"],   name="ix_piktsk_wave"),
            models.Index(fields=["ship_date"], name="ix_piktsk_sdt"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "PICK"

    def clean(self):

        super().clean()
        # 规范化
        if isinstance(self.wave_no, str):   self.wave_no   = self.wave_no.strip().upper()
        if isinstance(self.route_code, str):self.route_code= self.route_code.strip().upper()
        # 时间先后
        if self.ship_date and self.cutoff_at and self.cutoff_at.date() < self.ship_date:
            raise ValidationError({"cutoff_at": "截单时间不得早于计划出库日"})
class PickLineExtra(TaskLineExtraBase):
    from_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="pick_sources",
        verbose_name="拣货库位",
        null=True, blank=True,
    )
    # 允许 NULL；统一到 clean() 里 trim/upper
    from_lpn        = models.CharField("上游容器号", max_length=40, null=True, blank=True)
    to_container_no = models.CharField("目标容器/拣货框", max_length=40, null=True, blank=True)

    qty_picked = models.DecimalField("拣出数", max_digits=18, decimal_places=3, default=0)  # 统一 3 位
    qty_short  = models.DecimalField("短拣数",  max_digits=18, decimal_places=3, default=0)
    short_reason = models.CharField("短拣原因", max_length=40, blank=True, default="")

    class Meta:
        verbose_name = "拣货扩展"
        verbose_name_plural = "拣货扩展"
        constraints = [
            models.CheckConstraint(check=models.Q(qty_picked__gte=0), name="ck_pik_qpk_ge0"),
            models.CheckConstraint(check=models.Q(qty_short__gte=0),  name="ck_pik_qsh_ge0"),
            models.CheckConstraint(  # DB 兜底：短拣>0 则 reason 非空串
                name="ck_pik_short_reason",
                check=(models.Q(qty_short=0) | ~models.Q(short_reason="")),
            ),
        ]
        indexes = [
            models.Index(fields=["from_location"],             name="ix_pik_floc"),
            models.Index(fields=["from_lpn"],                  name="ix_pik_flpn"),
            models.Index(fields=["to_container_no"],           name="ix_pik_tcont"),
            models.Index(fields=["from_location", "from_lpn"], name="ix_pik_floc_lpn"),
        ]

    def __str__(self):
        # 只用 line_id，千万别去解引用 self.line（会在未赋值阶段触发异常）
        return f"PickLineExtra(line_id={self.line_id or '-'})"

    def _get_task_status(self):
        """
        返回任务头(WmsTask)的 status。
        优先用已缓存的 self.line.task；若未缓存，用 task_id 做一次轻量查询。
        """
        line = getattr(self, "line", None)
        if not line:
            return None

        # 1) 若已预取（select_related）过，直接用缓存对象，不额外查库
        task = getattr(line, "task", None)
        if task is not None and getattr(task, "status", None) is not None:
            return task.status

        # 2) 未缓存则用 task_id 轻量取单列（避免加载整个对象）
        task_id = getattr(line, "task_id", None)
        if task_id:
            # from allapp.tasking.models import WmsTask  # 就地导入避免循环依赖
            return (WmsTask.objects
                    .filter(pk=task_id)
                    .values_list("status", flat=True)
                    .first())
        return None

    @classmethod
    def expected_task_type(cls):
        return "PICK"

    def clean(self):

        super().clean()

        # 归一化文本
        def _norm_opt(s):
            s2 = (s or "").strip().upper()
            return s2 or None

        if isinstance(self.from_lpn, str):
            self.from_lpn = _norm_opt(self.from_lpn)
        if isinstance(self.to_container_no, str):
            self.to_container_no = _norm_opt(self.to_container_no)

        # 仓库一致性
        if self.from_location_id and self.line_id:
            if self.from_location.warehouse_id != self.line.task.warehouse_id:
                raise ValidationError({"from_location": "拣货库位不在本任务仓库"})

        # 业务建议：短拣>0 且无原因（去空白后）→ 报错（比 DB 约束更严格）
        if (self.qty_short or 0) > 0 and not (self.short_reason or "").strip():
            raise ValidationError({"short_reason": "短拣>0 时必须填写原因"})

    @transaction.atomic
    def save(self, *args, **kwargs):
        # 1) 严格校验
        self.full_clean()

        # 2) 先保存扩展本身
        ret = super().save(*args, **kwargs)
        total=getattr(self,'qty_picked',None)
        # 3) 同步到任务行进度（覆盖式重算）
        line = getattr(self, "line", None)
        if line_id := getattr(self, "line_id", None):
               # 行级并发保护（可选）
            (WmsTaskLine.objects
             .filter(pk=line_id)
             .update(qty_done=total))

            # 若当前实例(内存中的,上面是直接操作数据库)里还要马上读取最新值，手动回填：
            if line is not None:
                line.qty_done = total

                # 达到/超过计划 → 触发行完成收尾
                line = getattr(self, "line", None)  # 可能已 select_related 了
                qty_plan = getattr(line, "qty_plan", None)
                if qty_plan is None:
                    # 无计划也可按“>0即完成”的口径触发；如不需要可去掉
                    should_finish = (total > 0)
                else:
                    should_finish = (total >= qty_plan)

                if should_finish:
                    # by_user 从线程本地/请求上下文取；Admin 场景下可在表单保存 hook 内注入
                    by_user = getattr(self, "_by_user", None)  # 可由 Admin formset 注入
                    from allapp.tasking.services import finalize_receive_line
                    try:
                        from allapp.tasking.services import finalize_task_line
                        print("1103 before finalize_task_line")
                        result=finalize_task_line(self.line_id, by_user=by_user, trigger="AUTO_ON_REACH_PLAN")
                        print("1105 before if create_review_task(task)")
                        if result and result.get("task_status") == "COMPLETED":
                             task = getattr(line, "task", None)
                             print("1108 before create_review_task(task)")
                             self.create_review_task(task)
                             print("1110 after create_review_task(task)")
                    except ValidationError:
                        # 不阻断保存；把错误交给上游 Admin 动作提示即可
                        pass

        return ret

    @transaction.atomic
    def create_review_task(self,task):
        """
        创建复核任务，当拣货任务完成时调用。
        :param task: 完成的拣货任务对象
        """
        if task is None:
            return None

        # 创建复核任务
        # 获取需要的字段，确保 task 是一个 WmsTask 对象
        owner = task.owner if hasattr(task, 'owner') else None
        warehouse = task.warehouse if hasattr(task, 'warehouse') else None
        created_by = task.created_by if hasattr(task, 'created_by') else None
        task_lines = task.lines if hasattr(task, 'lines') else None
        biz_date = task.finished_at if task.finished_at else datetime.today().date()

        task_no = DocSequence.next_code(
            doc_type="FH",
            warehouse=warehouse,
            owner=owner,
            biz_date=biz_date,
        )
        print("before 创建复核任务 1140 create_review_task(task)")
        # 创建复核任务
        review_task = WmsTask.objects.create(
            task_no=task_no,
            task_type=WmsTask.TaskType.REVIEW,  # 假设复核任务类型为 REVIEW
            status=WmsTask.Status.DRAFT,  # 状态设为待处理
            owner=owner,  # 关联同一个货主
            warehouse=warehouse,  # 关联同一个仓库
            created_by=created_by,  # 设置创建人
            # review_status=WmsTask.ReviewStatus.NOT_READY,
            # posting_status=WmsTask.PostingStatus.NOT_READY,
        )
        print("review_task ",review_task)
        print("after 创建复核任务 1153 create_review_task(task)")

        # 为复核任务创建额外的任务信息（extra）
        ReviewTaskExtra.objects.create(
            task=review_task,
            # 根据需要设置复核任务的额外信息
            review_mode="AUTO",  # 假设复核模式是自动
            # 添加其他字段
        )
        print("将任务行信息复制到复核任务")
        # 将任务行信息复制到复核任务
        for task_line in task_lines.all():  # 假设 WmsTaskLine 是与任务关联的模型
            review_task_line = WmsTaskLine.objects.create(
                task=review_task,
                product=task_line.product,
                qty_plan=task_line.qty_plan,  # 复制拣货数量到复核任务
                # qty_done=task_line.qty_done,  # 复制拣货数量到复核任务
                from_location=task_line.from_location,  # 如果有位置的话
                to_location=task_line.to_location,  # 如果有位置的话
                status=WmsTaskLine.Status.DRAFT,  # 设置为待处理状态
                src_model="wmstaskline",
                src_id=getattr(task_line,"id",None)  # 关联拣货任务行
            )
            print("review_task_line ", review_task_line)
            print("# 为每个任务行创建额外的行信息（extraline）")
            # 为每个任务行创建额外的行信息（extraline）
            print("task_line.qty_plan,task_line.qty_done, ", task_line.qty_plan,task_line.qty_done)
            rl=ReviewLineExtra.objects.create(
                line=review_task_line,
                # 根据需要设置行的额外信息
                qty_plan_origin=task_line.qty_plan,  # 复制拣货数量到复核任务
                qty_picked_origin=task_line.qty_done,  # 复制拣货数量到复核任务
                # qty_reviewed=0,
                # qty_discrepancy_plan=0,
                # qty_discrepancy_picked=0,
                # 添加其他字段
            )
            print("rl rl.qty_plan,rl.qty_picked",rl.qty_plan_origin,rl.qty_picked_origin)
            print("after ReviewLineExtra.objects.create( # 为每个任务行创建额外的行信息（extraline）")
        print("before return  review_task")
        return review_task

    @transaction.atomic
    def delete(self, *args, **kwargs):
        # 一对一被删时，把行进度复位（你也可以改成保留原值或自定义规则）
        line_id = getattr(self, "line_id", None)
        ret = super().delete(*args, **kwargs)
        if line_id:
            from allapp.tasking.models import WmsTaskLine
            WmsTaskLine.objects.filter(pk=line_id).update(qty_done=0)
        return ret

# ==复核
class ReviewTaskExtra(TaskExtraBase):
    # 复核任务头扩展
    review_type = models.CharField(
        "复核类型", max_length=20,
        choices=[("SINGLE", "单次复核"), ("BATCH", "批量复核")],
        default="SINGLE"
    )
    review_date = models.DateField("复核日期", null=True, blank=True)
    reviewer = models.CharField("复核员", max_length=50, blank=True, default="")
    review_mode = models.CharField(max_length=50)  # 假设我们用 mode 而不是 review_mode

    class Meta:
        verbose_name = "复核任务头扩展"
        verbose_name_plural = "复核任务头扩展"
        indexes = [
            models.Index(fields=["review_type"], name="ix_rvtsk_type"),
            models.Index(fields=["review_date"], name="ix_rvtsk_date"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "REVIEW"

    def clean(self):
        super().clean()
        if self.review_date and self.review_date < timezone.now().date():
            raise ValidationError({"review_date": "复核日期不能早于今天"})
class ReviewLineExtra(TaskLineExtraBase):
    # 复核行扩展
    class REVIEW_Status(models.TextChoices):
        UNREVIEWED = "UnREVIEW", "未审核"
        REVIEWED       = "REVIEWED",       "已审核"

    from_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="review_sources",
        verbose_name="复核库位",
        null=True, blank=True,
    )
    from_lpn = models.CharField("上游容器号", max_length=40, null=True, blank=True)
    to_container_no = models.CharField("目标容器号", max_length=40, null=True, blank=True)

    qty_plan_origin = models.DecimalField("拣货计划数", max_digits=18, decimal_places=4, default=0)
    qty_picked_origin = models.DecimalField("拣货记录数", max_digits=18, decimal_places=4, default=0)
    qty_reviewed = models.DecimalField("复核数", max_digits=18, decimal_places=4, default=0)
    qty_discrepancy_plan = models.DecimalField("与计划差异", max_digits=18, decimal_places=4, default=0)
    qty_discrepancy_picked = models.DecimalField("与拣货记录差异", max_digits=18, decimal_places=4, default=0)
    discrepancy_reason = models.CharField("差异原因", max_length=40, blank=True, default="")
    review_status_rev= models.CharField(_("状态"), max_length=16, choices=REVIEW_Status.choices, db_index=True, default=REVIEW_Status.UNREVIEWED)

    class Meta:
        verbose_name = "复核任务扩展"
        verbose_name_plural = "复核任务扩展"
        constraints = [
            models.CheckConstraint(check=models.Q(qty_reviewed__gte=0), name="ck_rv_qr_ge0"),
               ]
        indexes = [
            models.Index(fields=["from_location"], name="ix_rv_floc"),
            models.Index(fields=["from_lpn"], name="ix_rv_flpn"),
            models.Index(fields=["to_container_no"], name="ix_rv_tcont"),
            models.Index(fields=["from_location", "from_lpn"], name="ix_rv_floc_lpn"),
        ]

    def __str__(self):
        return f"ReviewLineExtra(line_id={self.line_id or '-'})"

    def _get_task_status(self):
        """
        返回任务头(WmsTask)的 status。
        优先用已缓存的 self.line.task；若未缓存，用 task_id 做一次轻量查询。
        """
        line = getattr(self, "line", None)
        if not line:
            return None

        # 1) 若已预取（select_related）过，直接用缓存对象，不额外查库
        task = getattr(line, "task", None)
        if task is not None and getattr(task, "status", None) is not None:
            return task.status

        # 2) 未缓存则用 task_id 轻量取单列（避免加载整个对象）
        task_id = getattr(line, "task_id", None)
        if task_id:
            # from allapp.tasking.models import WmsTask  # 就地导入避免循环依赖
            return (WmsTask.objects
                    .filter(pk=task_id)
                    .values_list("status", flat=True)
                    .first())
        return None

    @classmethod
    def expected_task_type(cls):
        return "REVIEW"

    def clean(self):
        super().clean()

        def _norm_opt(s):
            s2 = (s or "").strip().upper()
            return s2 or None

        if isinstance(self.from_lpn, str):
            self.from_lpn = _norm_opt(self.from_lpn)
        if isinstance(self.to_container_no, str):
            self.to_container_no = _norm_opt(self.to_container_no)

        if self.from_location_id and self.line_id:
            if self.from_location.warehouse_id != self.line.task.warehouse_id:
                raise ValidationError({"from_location": "复核库位不在本任务仓库"})

        # if (self.qty_discrepancy or 0) > 0 and not (self.discrepancy_reason or "").strip():
        #     raise ValidationError({"discrepancy_reason": "差异数>0时必须填写原因"})

    @transaction.atomic
    def save(self, *args, **kwargs):
        print("*********************Save method called!")  # 检查是否在页面加载时调用
        def _to_dec(x):
            return x if isinstance(x, Decimal) else Decimal(x or 0)

        update_fields = kwargs.get("update_fields")

        if  _to_dec(self.qty_reviewed) > 0:
            self.review_status_rev = self.REVIEW_Status.REVIEWED
            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.add("review_status_rev")
                kwargs["update_fields"] = list(update_fields)

        self.full_clean()  # 确保先进行数据验证
        ret = super().save(*args, **kwargs)

        line = getattr(self, "line", None)
        task = getattr(line, "task", None) if line else None

        discrepancy_plan = Decimal(self.qty_discrepancy_plan or 0)
        discrepancy_picked = Decimal(self.qty_discrepancy_picked or 0)
        has_discrepancy = bool(discrepancy_plan) or bool(discrepancy_picked)
        print("1338 has_discrepancy and line and task:")
        if has_discrepancy and line and task:
            product = getattr(line, "product", None)
            location = (
                    self.from_location
                    or getattr(line, "from_location", None)
                    or getattr(line, "to_location", None)
            )

            if product and location:
                from allapp.inventory.models import ReviewDifference, ReviewDifferenceLine
                biz_date = datetime.today().date()
                order_no = DocSequence.next_code(
                    doc_type="FH",
                    warehouse=task.warehouse,
                    owner=task.owner,
                    biz_date=biz_date,
                )

                review_diff = ReviewDifference.objects.create(
                    order_no=order_no,
                    warehouse=task.warehouse,
                    reviewed_by=None,
                    status=ReviewDifference.Status.PENDING,
                    reason=self.discrepancy_reason or "",
                    note=f"来源任务:{task.task_no}, 行:{line.id}",
                )

                def _to_decimal(value) -> Decimal:
                    if value is None:
                        return Decimal(0)
                    if isinstance(value, Decimal):
                        return value
                    return Decimal(str(value))

                def _quantize_4(value: Decimal) -> Decimal:
                    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

                quantity_difference = (
                    discrepancy_picked if discrepancy_picked else discrepancy_plan
                )

                ReviewDifferenceLine.objects.create(
                    recheck_order=review_diff,
                    product=product,
                    location=location,
                    batch_no=None,
                    serial_no=None,
                    quantity_before=_quantize_4(_to_decimal(self.qty_picked_origin)),
                    quantity_after=_quantize_4(_to_decimal(self.qty_reviewed)),
                    quantity_difference=_quantize_4(_to_decimal(quantity_difference)),
                    status=ReviewDifference.Status.PENDING,
                )
        print("0 1390 if task:")
        if task:
            line_ids = task.lines.values_list("id", flat=True)
            print("0 pending_exists::")
            pending_exists = (
                self.__class__.objects
                .filter(line_id__in=line_ids)
                .exclude(review_status_rev=self.REVIEW_Status.REVIEWED)
                .exists()
            )
            print("1 pending_exists::")
            if not pending_exists:
                WmsTask.objects.filter(pk=task.pk).update(
                    status=WmsTask.Status.COMPLETED,
                    review_status=WmsTask.ReviewStatus.APPROVED,
                    posting_status=WmsTask.PostingStatus.PENDING,
                )
                print("0 PackTaskExtra.create_followup_from_review(task):")
                PackTaskExtra.create_followup_from_review(task)
                print("1 PackTaskExtra.create_followup_from_review(task):")

        print("1 if task:")
        return ret

    @transaction.atomic
    def delete(self, *args, **kwargs):
        line_id = getattr(self, "line_id", None)
        ret = super().delete(*args, **kwargs)
        if line_id:
            from allapp.tasking.models import WmsTaskLine
            WmsTaskLine.objects.filter(pk=line_id).update(qty_done=0)
        return ret

# ==打包
class PackTaskExtra(TaskExtraBase):
    """打包头：承运/服务/标签模板/默认包材 + 复核策略"""
    PACK_CHECK_POLICY = [
        ("SELF", "自检（不需二次复核）"),
        ("DOUBLE", "双人全检"),
        ("RANDOM", "抽检"),
        ("NONE", "不复核"),
    ]
    default_carrier_code  = models.CharField("默认承运商", max_length=20, blank=True, default="")
    default_service_level = models.CharField("默认服务级别", max_length=20, blank=True, default="")
    label_tpl_code        = models.CharField("标签模板", max_length=40, blank=True, default="")
    default_pack_code     = models.CharField("默认包材", max_length=30, blank=True, default="")

    check_policy = models.CharField("复核策略", max_length=10, choices=PACK_CHECK_POLICY, default="SELF")
    check_ratio  = models.DecimalField("抽检比例(0~1]", max_digits=4, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = "打包任务头扩展"
        verbose_name_plural = "打包任务头扩展"
        constraints = [
            # OneToOne 已唯一，去掉重复 UniqueConstraint
            # 服务级别 ⇒ 必须有承运商
            models.CheckConstraint(
                name="ck_pak_srv_car",
                check=(models.Q(default_service_level="") | ~models.Q(default_carrier_code="")),
            ),
            # RANDOM ⇒ 0<ratio<=1；非 RANDOM ⇒ ratio 可空
            models.CheckConstraint(
                name="ck_pak_chk_ratio",
                check=(
                    ~models.Q(check_policy="RANDOM")
                    | (models.Q(check_ratio__isnull=False) & models.Q(check_ratio__gt=0) & models.Q(check_ratio__lte=1))
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["default_carrier_code"], name="ix_pak_car"),
            models.Index(fields=["check_policy"], name="ix_pak_ckpol"),
        ]

    @classmethod
    def expected_task_type(cls): return "PACK"

    def clean(self):
        super().clean()
        # 规范化，避免空格绕过约束
        for f in ("default_carrier_code", "default_service_level", "label_tpl_code", "default_pack_code"):
            v = getattr(self, f, "")
            if isinstance(v, str):
                setattr(self, f, v.strip().upper())
        # RANDOM 必须给比例；其余可空（DB 已兜底，这里给更友好的报错）
        if self.check_policy == "RANDOM" and not self.check_ratio:
            raise ValidationError({"check_ratio": "抽检策略为 RANDOM 时必须填写抽检比例(0~1]"})


    @transaction.atomic
    def save(self, *args, **kwargs):
        return super().save(*args, **kwargs)

    @classmethod
    @transaction.atomic
    def create_followup_from_review(cls, review_task):
        if not review_task:
            return None

        if not isinstance(review_task, WmsTask):
            review_task = (WmsTask.objects
                           .select_related("owner", "warehouse")
                           .filter(pk=getattr(review_task, "pk", review_task))
                           .first())
        else:
            review_task = (WmsTask.objects
                           .select_related("owner", "warehouse")
                           .filter(pk=review_task.pk)
                           .first())

        if not review_task:
            return None

        review_status = getattr(review_task, "review_status", None)
        if review_status != WmsTask.ReviewStatus.APPROVED:
            return None

        line_extras = (
            ReviewLineExtra.objects
            .select_related("line__product", "line__from_location", "line__to_location")
            .filter(line__task=review_task)
        )

        pack_payload: list[dict] = []
        dispatch_payload: list[dict] = []

        for extra in line_extras:
            line = getattr(extra, "line", None)
            if line is None or not getattr(line, "product_id", None):
                continue

            qty = Decimal(extra.qty_reviewed or 0)
            if qty <= 0:
                continue

            product = getattr(line, "product", None)
            pack_requirement = getattr(product, "pack_requirement", None)

            line_data = {
                "product_id": line.product_id,
                "qty": qty,
                "src_line_id": line.id,
                "from_location_id": getattr(line, "to_location_id", None) or getattr(line, "from_location_id", None),
                "to_location_id": None,
            }

            if pack_requirement and str(pack_requirement).upper() != "NONE":
                pack_payload.append(line_data)
            else:
                dispatch_payload.append(line_data)

        if not pack_payload and not dispatch_payload:
            return None

        biz_datetime = getattr(review_task, "finished_at", None) or timezone.now()
        if timezone.is_naive(biz_datetime):
            biz_datetime = timezone.make_aware(biz_datetime, timezone.get_current_timezone())
        biz_date = biz_datetime.date()

        source_pk = str(review_task.pk)

        created = {}

        if pack_payload:
            pack_exists = WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PACK,
                source_model="WmsTask",
                source_pk=source_pk,
            ).exists()

            if not pack_exists:
                pack_task_no = DocSequence.next_code(
                    doc_type="PKG",
                    warehouse=review_task.warehouse,
                    owner=review_task.owner,
                    biz_date=biz_date,
                )

                pack_task = WmsTask.objects.create(
                    task_no=pack_task_no,
                    task_type=WmsTask.TaskType.PACK,
                    owner=review_task.owner,
                    warehouse=review_task.warehouse,
                    status=WmsTask.Status.DRAFT,
                    source_app="tasking",
                    source_model="WmsTask",
                    source_pk=source_pk,
                    created_by=review_task.created_by,
                    # review_status=WmsTask.ReviewStatus.NOT_READY,
                    # posting_status=WmsTask.PostingStatus.NOT_READY,
                )

                cls.objects.create(task=pack_task)

                for item in pack_payload:
                    WmsTaskLine.objects.create(
                        task=pack_task,
                        product_id=item["product_id"],
                        qty_plan=item["qty"],
                        from_location_id=item.get("from_location_id"),
                        to_location_id=item.get("to_location_id"),
                        status=WmsTaskLine.Status.DRAFT,
                        src_model="WmsTaskLine",
                        src_id=item["src_line_id"],
                    )

                created["pack_task"] = pack_task

        if dispatch_payload:
            dispatch_exists = WmsTask.objects.filter(
                task_type=WmsTask.TaskType.DISPATCH,
                source_model="WmsTask",
                source_pk=source_pk,
            ).exists()

            if not dispatch_exists:
                dispatch_task_no = DocSequence.next_code(
                    doc_type="FY",
                    warehouse=review_task.warehouse,
                    owner=review_task.owner,
                    biz_date=biz_date,
                )

                dispatch_task = WmsTask.objects.create(
                    task_no=dispatch_task_no,
                    task_type=WmsTask.TaskType.DISPATCH,
                    owner=review_task.owner,
                    warehouse=review_task.warehouse,
                    status=WmsTask.Status.DRAFT,
                    source_app="tasking",
                    source_model="WmsTask",
                    source_pk=source_pk,
                    created_by=review_task.created_by,
                    # review_status=WmsTask.ReviewStatus.NOT_READY,
                    # posting_status=WmsTask.PostingStatus.NOT_READY,
                )

                DispatchTaskExtra.objects.create(task=dispatch_task)

                for item in dispatch_payload:
                    WmsTaskLine.objects.create(
                        task=dispatch_task,
                        product_id=item["product_id"],
                        qty_plan=item["qty"],
                        from_location_id=item.get("from_location_id"),
                        to_location_id=item.get("to_location_id"),
                        status=WmsTaskLine.Status.DRAFT,
                        src_model="WmsTaskLine",
                        src_id=item["src_line_id"],
                    )

                created["dispatch_task"] = dispatch_task

        return created or None

class PackLineExtra(TaskLineExtraBase):
    to_container = models.ForeignKey(
        "locations.Container",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="pack_main_lines",
        verbose_name="主容器"
    )
    # 建议去掉字段层 db_index，统一在 Meta.indexes 建索引
    to_container_no = models.CharField("主容器号(冗余)", max_length=60, blank=True, default="")

    # 包装层级快照（命名保留，但注明：这是 ProductPackage 不是 UoM）
    aux_uom = models.ForeignKey(
        "products.ProductPackage",
        on_delete=models.PROTECT,
        null=True, blank=True,
        verbose_name="包装单位(快照)"
    )
    ratio = models.DecimalField("换算率(包装→基本,快照)", max_digits=14, decimal_places=4, null=True, blank=True)

    class Meta:
        verbose_name = "打包扩展"
        verbose_name_plural = "打包扩展"
        constraints = [
            # OneToOne(line) 已唯一，去掉重复 UniqueConstraint
            models.CheckConstraint(
                check=models.Q(ratio__isnull=True) | models.Q(ratio__gt=0),
                name="ck_pack_ratio_pos"
            ),
        ]
        indexes = [
            models.Index(fields=["to_container"], name="ix_pack_tcont"),
            models.Index(fields=["to_container_no"], name="ix_pack_tcontno"),
        ]

    @classmethod
    def expected_task_type(cls): return "PACK"

    def clean(self):
        super().clean()

        # 容器仓库一致
        if self.to_container_id and self.line_id:
            if self.to_container.warehouse_id != self.line.task.warehouse_id:
                raise ValidationError({"to_container": "容器不在任务仓库"})

        # 容器号一致性（两边都规范化）
        def _norm(s: str | None) -> str | None:
            s = (s or "").strip().upper()
            return s or None

        if isinstance(self.to_container_no, str):
            self.to_container_no = _norm(self.to_container_no)

        if self.to_container_id and self.to_container_no:
            if _norm(self.to_container.container_no) != self.to_container_no:
                raise ValidationError({"to_container_no": "容器号与所选容器不一致"})

        # 包装单位必须属于该商品（若行绑定了商品）
        if self.aux_uom_id and getattr(self.line, "product_id", None):
            if self.aux_uom.product_id != self.line.product_id:
                raise ValidationError({"aux_uom": "包装单位必须属于该商品"})

    def save(self, *args, **kwargs):
        # 自动带出容器号快照
        if self.to_container_id and not self.to_container_no:
            self.to_container_no = (self.to_container.container_no or "").strip().upper() or None

        # 自动回填比率快照
        if self.aux_uom_id and not self.ratio:
            self.ratio = Decimal(self.aux_uom.qty_in_base)

        self.full_clean()
        return super().save(*args, **kwargs)

# ==装车
class LoadTaskExtra(TaskExtraBase):
    """装车任务头扩展"""
    trip_no    = models.CharField("车次号",   max_length=40, blank=True, default="")
    vehicle_no = models.CharField("车牌号",   max_length=20, blank=True, default="")
    dock_code  = models.CharField("月台编码", max_length=20, blank=True, default="")
    depart_eta = models.DateTimeField("预计发车", null=True, blank=True)

    class Meta:
        verbose_name = "装车任务头扩展"
        verbose_name_plural = "装车任务头扩展"
        indexes = [
            models.Index(fields=["trip_no"],    name="ix_loadtsk_trip"),
            models.Index(fields=["vehicle_no"], name="ix_loadtsk_veh"),
            models.Index(fields=["dock_code"],  name="ix_loadtsk_dock"),
            models.Index(fields=["depart_eta"], name="ix_loadtsk_eta"),
        ]

    @classmethod
    def expected_task_type(cls): return "LOAD"

    def clean(self):
        super().clean()
        # 规范化
        for f in ("trip_no", "vehicle_no", "dock_code"):
            v = getattr(self, f, "")
            if isinstance(v, str):
                setattr(self, f, v.strip().upper())

        if not self.depart_eta:
            return

        # 统一成 aware 再比较
        eta = self.depart_eta
        if timezone.is_naive(eta):
            eta = timezone.make_aware(eta, timezone.get_current_timezone())

        now = timezone.now()  # aware
        if eta <= now:
            raise ValidationError({"depart_eta": "预计发车时间必须晚于当前时间"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

class LoadLineExtra(TaskLineExtraBase):
    """装车行扩展"""
    to_container = models.ForeignKey(
        "locations.Container", on_delete=models.PROTECT,
        null=True, blank=True, related_name="load_lines", verbose_name="目标容器"
    )
    to_container_no   = models.CharField("目标容器号(冗余)", max_length=60, blank=True, default="")
    container_seal_no = models.CharField("容器封签号(可选)", max_length=30, blank=True, default="")

    loaded_at = models.DateTimeField("装载时间", null=True, blank=True)
    loaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="loaded_lines", verbose_name="装载人"
    )
    gross_weight_kg = models.DecimalField("毛重(kg)", max_digits=10, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = "装车扩展"
        verbose_name_plural = "装车扩展"
        constraints = [
            # OneToOne(line) 已唯一，去掉重复 UniqueConstraint
            models.CheckConstraint(
                check=models.Q(gross_weight_kg__isnull=True) | models.Q(gross_weight_kg__gte=0),
                name="ck_load_gw_nonneg",
            ),
        ]
        indexes = [
            models.Index(fields=["to_container"],      name="ix_loa_tcont"),
            models.Index(fields=["to_container_no"],   name="ix_loa_tcontno"),
            models.Index(fields=["container_seal_no"], name="ix_loa_cseal"),
        ]

    @classmethod
    def expected_task_type(cls): return "LOAD"

    def clean(self):
        super().clean()

        # 仓库一致
        if self.to_container_id and self.line_id:
            if self.to_container.warehouse_id != self.line.task.warehouse_id:
                raise ValidationError({"to_container": "容器不在本任务的仓库"})

        # 容器号一致性（双向规范化）
        def _norm(s: str | None) -> str | None:
            s = (s or "").strip().upper()
            return s or None

        if isinstance(self.to_container_no, str):
            self.to_container_no = _norm(self.to_container_no)

        if self.to_container_id and self.to_container_no:
            if _norm(self.to_container.container_no) != self.to_container_no:
                raise ValidationError({"to_container_no": "容器号与所选容器不一致"})

    def save(self, *args, **kwargs):
        # 自动回填容器号快照
        if self.to_container_id and not self.to_container_no:
            self.to_container_no = (self.to_container.container_no or "").strip().upper() or ""

        self.full_clean()
        return super().save(*args, **kwargs)

# ==发运
class DispatchTaskExtra(TaskExtraBase):
    """发运头：交接清单/承运/服务/波次"""
    manifest_no  = models.CharField("交接清单号", max_length=40, blank=True, default="")
    carrier_code = models.CharField("承运商",     max_length=20, blank=True, default="")
    service_level= models.CharField("服务级别",   max_length=20, blank=True, default="")
    wave_no      = models.CharField("波次号",     max_length=30, blank=True, default="")

    class Meta:
        verbose_name = "发运任务头扩展"
        verbose_name_plural = "发运任务头扩展"
        constraints = [
            models.UniqueConstraint(fields=["task"], name="ux_dsptsk_task"),
            models.CheckConstraint(
                name="ck_dsp_srv_car",
                check=(models.Q(service_level="") | ~models.Q(carrier_code="")),
            ),
        ]
        indexes = [
            models.Index(fields=["manifest_no"],  name="ix_dsptsk_man"),
            models.Index(fields=["carrier_code"], name="ix_dsptsk_car"),
            models.Index(fields=["wave_no"],      name="ix_dsptsk_wave"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "DISPATCH"

    def clean(self):
        super().clean()
        # 规范化：大写+去空格
        for f in ("manifest_no", "carrier_code", "wave_no"):
            v = getattr(self, f, "")
            if isinstance(v, str):
                setattr(self, f, v.strip().upper())

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

class DispatchLineExtra(TaskLineExtraBase):
    """发运行扩展：包裹容器+运单信息+交接审计"""
    package_container = models.ForeignKey(
        "locations.Container", on_delete=models.PROTECT,
        null=True, blank=True, related_name="dispatch_lines", verbose_name="包裹容器"
    )
    package_lpn = models.CharField("包裹LPN", max_length=60, blank=True, default="")

    carrier_code = models.CharField("承运商(可覆盖头)", max_length=20, blank=True, default="")
    waybill_no   = models.CharField("运单号", max_length=80, blank=True, default="")
    route_code   = models.CharField("线路编码(可覆盖头)", max_length=20, blank=True, default="")

    qty_dispatch = models.DecimalField("发运数", max_digits=18, decimal_places=4, default=0)
    base_uom = models.ForeignKey(ProductUom, verbose_name="基本单位" , on_delete=models.PROTECT, related_name="dispatch_base_of_products")
    piece_no     = models.PositiveIntegerField("分件序号", null=True, blank=True)
    piece_total  = models.PositiveIntegerField("总件数",   null=True, blank=True)

    handed_over_at = models.DateTimeField("交接时间", null=True, blank=True)
    handed_over_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="dispatch_handover_done", verbose_name="交接人"
    )

    class Meta:
        verbose_name = "发运扩展"
        verbose_name_plural = "发运扩展"
        constraints = [
            # OneToOne(line) 已唯一，这里不再重复 UniqueConstraint
            models.CheckConstraint(
                name="ck_dsp_piece_consistency",
                check=(
                    (Q(piece_no__isnull=True) & Q(piece_total__isnull=True)) |
                    (Q(piece_no__gt=0) & Q(piece_total__gt=0) & Q(piece_no__lte=F("piece_total")))
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["package_container"], name="ix_dsp_pkg_cont"),
            models.Index(fields=["package_lpn"],       name="ix_dsp_pkg_lpn"),
            models.Index(fields=["carrier_code", "waybill_no"], name="ix_dsp_car_wb"),
            models.Index(fields=["route_code"], name="ix_dsp_route"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "DISPATCH"

    def clean(self):
        super().clean()

        # 规范化辅助
        def _norm(s: str | None) -> str:
            return (s or "").strip().upper()

        # 1) 容器仓库一致
        if self.package_container_id and self.line_id:
            if self.package_container.warehouse_id != self.line.task.warehouse_id:
                raise ValidationError({"package_container": "包裹容器不在本任务仓库"})

        # 2) 容器号一致性
        if self.package_container_id and self.package_lpn:
            if _norm(self.package_lpn) != _norm(self.package_container.container_no):
                raise ValidationError({"package_lpn": "包裹LPN与所选容器不一致"})

        # 3) 至少给“容器/LPN/运单号”其一
        if not (self.package_container_id or self.package_lpn.strip() or self.waybill_no.strip()):
            raise ValidationError({"__all__": "必须提供包裹容器/LPN 或 运单号其一"})

        # 4) 分件信息需有运单号
        if (self.piece_no or self.piece_total) and not self.waybill_no.strip():
            raise ValidationError({"waybill_no": "使用分件信息时必须提供运单号"})

    def save(self, *args, **kwargs):
        # 1) 严格校验
        self.full_clean()
        # 归一化编码：LPN/承运/线路常用大写，运单号保持大小写但去空格
        if self.package_lpn: self.package_lpn = self.package_lpn.strip().upper()
        if self.carrier_code: self.carrier_code = self.carrier_code.strip().upper()
        if self.route_code:   self.route_code   = self.route_code.strip().upper()
        if self.waybill_no:   self.waybill_no   = self.waybill_no.strip()

        # 有容器但没填 LPN 时，冗余回填
        if self.package_container_id and not self.package_lpn:
            self.package_lpn = (self.package_container.container_no or "").strip().upper()


        # 2) 先保存扩展本身
        ret = super().save(*args, **kwargs)
        total = getattr(self, 'qty_dispatch', None)
        # 3) 同步到任务行进度（覆盖式重算）
        line = getattr(self, "line", None)
        if line_id := getattr(self, "line_id", None):
            # 行级并发保护（可选）
            (WmsTaskLine.objects
             .filter(pk=line_id)
             .update(qty_done=total))

            # 若当前实例(内存中的,上面是直接操作数据库)里还要马上读取最新值，手动回填：
            if line is not None:
                line.qty_done = total

                # 达到/超过计划 → 触发行完成收尾
                line = getattr(self, "line", None)  # 可能已 select_related 了
                qty_plan = getattr(line, "qty_plan", None)
                if qty_plan is None:
                    # 无计划也可按“>0即完成”的口径触发；如不需要可去掉
                    should_finish = (total > 0)
                else:
                    should_finish = (total >= qty_plan)

                if should_finish:
                    # by_user 从线程本地/请求上下文取；Admin 场景下可在表单保存 hook 内注入
                    by_user = getattr(self, "_by_user", None)  # 可由 Admin formset 注入
                    from allapp.tasking.services import finalize_receive_line
                    try:
                        from allapp.tasking.services import finalize_task_line
                        print(" before finalize_task_line")
                        result = finalize_task_line(self.line_id, by_user=by_user, trigger="AUTO_ON_REACH_PLAN")
                        print(" before if create_review_task(task)")
                        # if result and result.get("task_status") == "COMPLETED":

                    except ValidationError:
                        # 不阻断保存；把错误交给上游 Admin 动作提示即可
                        pass

        return ret


# ==补货
class ReplenishTaskExtra(TaskExtraBase):
    """补货头：触发类型/默认来源目标区域/策略码（头部只放默认与策略）"""
    trigger = models.CharField(
        "触发类型", max_length=10,
        choices=[("MINMAX", "最小最大"), ("DEMAND", "需求驱动")],
        default="MINMAX"
    )
    src_zone    = models.CharField("默认来源区域", max_length=20, blank=True, default="")
    dst_zone    = models.CharField("默认目标区域", max_length=20, blank=True, default="")
    policy_code = models.CharField("策略码", max_length=20, blank=True, default="")

    class Meta:
        verbose_name = "补货任务头扩展"
        verbose_name_plural = "补货任务头扩展"
        constraints = [
            models.UniqueConstraint(fields=["task"], name="ux_rpltsk_task"),
            models.CheckConstraint(
                name="ck_rpl_zones_diff",
                check=(models.Q(src_zone="") | models.Q(dst_zone="") | ~models.Q(src_zone=models.F("dst_zone"))),
            ),
        ]
        indexes = [
            models.Index(fields=["src_zone"],    name="ix_rpltsk_src"),
            models.Index(fields=["dst_zone"],    name="ix_rpltsk_dst"),
            models.Index(fields=["policy_code"], name="ix_rpltsk_pol"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "REPLEN"

    def clean(self):
        super().clean()
        # 统一大小写与去空白
        for f in ("src_zone", "dst_zone", "policy_code"):
            v = getattr(self, f, "")
            if isinstance(v, str):
                setattr(self, f, v.strip().upper())

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
class ReplenishLineExtra(TaskLineExtraBase):
    """补货：来源/目标库位与（可选）LPN，及移动数量（基本单位）"""

    from_location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT,
        related_name="replen_sources", verbose_name="来源库位",
        null=True, blank=True,
    )
    to_location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT,
        related_name="replen_targets", verbose_name="目标库位",
        null=True, blank=True,
    )

    from_lpn = models.CharField("上游容器号", max_length=40, blank=True, default="", db_index=True)
    to_lpn   = models.CharField("目标容器号", max_length=40, blank=True, default="", db_index=True)

    # 展开 DEC_QTY
    qty_move = models.DecimalField("补货数", max_digits=14, decimal_places=4, default=0)

    class Meta:
        verbose_name = "补货扩展"
        verbose_name_plural = "补货扩展"
        constraints = [
            # line 是 OneToOne，不需要再加唯一约束
            models.CheckConstraint(check=Q(qty_move__gte=0), name="ck_rpl_qmv_ge0"),
            models.CheckConstraint(
                name="ck_rpl_has_from",
                check=Q(from_location__isnull=False) | ~Q(from_lpn=""),
            ),
            models.CheckConstraint(
                name="ck_rpl_has_to",
                check=Q(to_location__isnull=False) | ~Q(to_lpn=""),
            ),
        ]
        indexes = [
            models.Index(fields=["from_location"], name="ix_rpl_floc"),
            models.Index(fields=["to_location"],   name="ix_rpl_tloc"),
            models.Index(fields=["from_lpn"],      name="ix_rpl_flpn"),
            models.Index(fields=["to_lpn"],        name="ix_rpl_tlpn"),
            models.Index(fields=["from_location", "to_location"], name="ix_rpl_floc_tloc"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "REPLEN"

    def clean(self):
        super().clean()

        # —— 1) 仓库一致性 —— #
        if self.line_id:
            task_wh_id = self.line.task.warehouse_id
            if self.from_location_id:
                if self.from_location.warehouse_id != task_wh_id:
                    raise ValidationError({"from_location": "来源库位不在该任务仓库"})
            if self.to_location_id:
                if self.to_location.warehouse_id != task_wh_id:
                    raise ValidationError({"to_location": "目标库位不在该任务仓库"})

        # —— 2) 必须发生“位移” —— #
        f_lpn = (self.from_lpn or "").strip().upper()
        t_lpn = (self.to_lpn or "").strip().upper()
        same_loc = (
            self.from_location_id and self.to_location_id and
            self.from_location_id == self.to_location_id
        ) or (not self.from_location_id and not self.to_location_id)
        same_lpn = (f_lpn == t_lpn)

        if same_loc and same_lpn:
            raise ValidationError({"__all__": "来源与目标完全一致，无法补货（请变更库位或LPN其一）"})

        # （可选）若要强制 >0：
        # from decimal import Decimal
        # if (self.qty_move or Decimal("0")) <= Decimal("0"):
        #     raise ValidationError({"qty_move": "补货数量必须大于 0"})

    def save(self, *args, **kwargs):
        # 归一化 LPN
        if self.from_lpn:
            self.from_lpn = self.from_lpn.strip().upper()
        if self.to_lpn:
            self.to_lpn = self.to_lpn.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)

# ==移位
class RelocTaskExtra(TaskExtraBase):
    """移仓/移库任务头：默认来源/目标区域 + 策略/原因"""
    src_zone    = models.CharField("默认来源区域", max_length=20, blank=True, default="")
    dst_zone    = models.CharField("默认目标区域", max_length=20, blank=True, default="")
    policy_code = models.CharField("策略码", max_length=20, blank=True, default="")
    reason_code = models.CharField("原因码", max_length=20, blank=True, default="")

    class Meta:
        verbose_name = "移仓任务头扩展"
        verbose_name_plural = "移仓任务头扩展"
        constraints = [
            models.UniqueConstraint(fields=["task"], name="ux_rlctsk_task"),
            models.CheckConstraint(
                name="ck_rlc_zones_diff",
                check=(models.Q(src_zone="") | models.Q(dst_zone="") | ~models.Q(src_zone=models.F("dst_zone"))),
            ),
        ]
        indexes = [
            models.Index(fields=["src_zone"],    name="ix_rlctsk_src"),
            models.Index(fields=["dst_zone"],    name="ix_rlctsk_dst"),
            models.Index(fields=["policy_code"], name="ix_rlctsk_pol"),
            models.Index(fields=["reason_code"], name="ix_rlctsk_rsn"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "RELOC"

    def clean(self):
        super().clean()
        # 统一大小写/去空格
        for f in ("src_zone", "dst_zone", "policy_code", "reason_code"):
            v = getattr(self, f, "")
            if isinstance(v, str):
                setattr(self, f, v.strip().upper())

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
class RelocLineExtra(TaskLineExtraBase):
    """
    移仓/移库 行 Extra（轻量）：
    - 记录来源/目标库位 + （可选）来源/目标容器号
    - 移动数量（基本单位）
    - 行级原因码（覆盖头部 reason_code）
    """
    from_location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT,
        related_name="reloc_sources", verbose_name="来源库位",
        null=True, blank=True,
    )
    to_location = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT,
        related_name="reloc_targets", verbose_name="目标库位",
        null=True, blank=True,
    )

    from_lpn = models.CharField("上游容器号", max_length=40, blank=True, default="", db_index=True)
    to_lpn   = models.CharField("目标容器号", max_length=40, blank=True, default="", db_index=True)

    # 展开 DEC_QTY
    qty_move = models.DecimalField("移动数量", max_digits=14, decimal_places=4, default=0)

    reason_code = models.CharField("行原因码(可覆盖头)", max_length=20, blank=True, default="")

    class Meta:
        verbose_name = "移库扩展"
        verbose_name_plural = "移库扩展"
        constraints = [
            # line 是 OneToOne，无需再加唯一约束
            models.CheckConstraint(check=Q(qty_move__gte=0), name="ck_rlc_qmv_ge0"),
            models.CheckConstraint(
                name="ck_rlc_has_from",
                check=Q(from_location__isnull=False) | ~Q(from_lpn=""),
            ),
            models.CheckConstraint(
                name="ck_rlc_has_to",
                check=Q(to_location__isnull=False) | ~Q(to_lpn=""),
            ),
        ]
        indexes = [
            models.Index(fields=["from_location"], name="ix_rlc_floc"),
            models.Index(fields=["to_location"],   name="ix_rlc_tloc"),
            models.Index(fields=["from_lpn"],      name="ix_rlc_flpn"),
            models.Index(fields=["to_lpn"],        name="ix_rlc_tlpn"),
            models.Index(fields=["from_location", "to_location"], name="ix_rlc_floc_tloc"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "RELOC"

    def clean(self):
        super().clean()

        # 1) 仓库一致性
        if self.line_id:
            task_wh_id = self.line.task.warehouse_id
            if self.from_location_id and self.from_location.warehouse_id != task_wh_id:
                raise ValidationError({"from_location": "来源库位不在该任务仓库"})
            if self.to_location_id and self.to_location.warehouse_id != task_wh_id:
                raise ValidationError({"to_location": "目标库位不在该任务仓库"})

        # 2) 必须发生“位移”
        f_lpn = (self.from_lpn or "").strip().upper()
        t_lpn = (self.to_lpn or "").strip().upper()
        same_loc = (
            (self.from_location_id and self.to_location_id and self.from_location_id == self.to_location_id)
        ) or (not self.from_location_id and not self.to_location_id)
        same_lpn = (f_lpn == t_lpn)
        if same_loc and same_lpn:
            raise ValidationError({"__all__": "来源与目标完全一致，无法移库（请变更库位或LPN其一）"})

        # 3) 与任务头默认区域的软一致性（仅当你能拿到头部 Extra）
        #    注意 related_name="%(class)s" 生成的是小写名：reloctaskextra

        # （可选）强制 >0
        # from decimal import Decimal
        # if (self.qty_move or Decimal("0")) <= Decimal("0"):
        #     raise ValidationError({"qty_move": "移动数量必须大于 0"})

    def save(self, *args, **kwargs):
        # 归一化 LPN
        if self.from_lpn:
            self.from_lpn = self.from_lpn.strip().upper()
        if self.to_lpn:
            self.to_lpn = self.to_lpn.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)

# ==盘点头
class CountTaskExtra(TaskExtraBase):
    """盘点头：范围/方式/是否冻结/复盘阈值等（统一任务级过账）"""
    scope = models.CharField(
        "盘点范围", max_length=10,
        choices=[("LOC", "库位"), ("ZONE", "区域"), ("SKU", "商品"), ("ALL", "全仓")],
        default="LOC"
    )
    blind = models.BooleanField("盲盘", default=True)
    freeze = models.BooleanField("冻结库存", default=True)
    recount_threshold = models.DecimalField("复盘阈值(绝对数)", max_digits=14, decimal_places=4, default=0)

    class Meta:
        verbose_name = "盘点任务头扩展"
        verbose_name_plural = "盘点任务头扩展"
        constraints = [
            # OneToOne 已保证唯一，这条可以删除；若保留也不报错，但会多一次迁移
            # models.UniqueConstraint(fields=["task"], name="ux_cnttsk_task"),
            models.CheckConstraint(
                name="ck_cnt_recount_ge0",
                check=Q(recount_threshold__gte=0),
            ),
        ]
        indexes = [
            models.Index(fields=["scope"], name="ix_cnttsk_scope"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "COUNT"
# ==盘点行
class CountLineExtra(TaskLineExtraBase):
    """盘点：账面/实盘/差异 + 可选批次/效期/LPN"""
    class CountOrder(models.TextChoices):
        FIRST   = "FIRST",   "首次盘点"
        SECOND  = "SECOND",  "第二次盘点"
        THIRD   = "THIRD",   "第三次盘点"

    lot_no   = models.CharField("批号", max_length=60, blank=True, default="", db_index=True)
    exp_date = models.DateField("有效期至", null=True, blank=True, db_index=True)
    lpn_no   = models.CharField("LPN/容器号", max_length=60, blank=True, default="", db_index=True)

    # 展开 DEC_QTY，避免未定义
    qty_counted = models.DecimalField("实盘数", max_digits=18, decimal_places=4, default=Decimal("0"))
    qty_book    = models.DecimalField("账面数(快照)", max_digits=18, decimal_places=4, default=Decimal("0"))
    qty_diff    = models.DecimalField("差异=实盘-账面", max_digits=18, decimal_places=4, default=Decimal("0"))

    count_status=models.CharField( "是否已盘点", max_length=20, choices=[("NOT_COUNTED", "未盘"), ("COUNTED", "已盘")],default="NOT_COUNTED",)
    method = models.CharField( "盘点方式", max_length=10, choices=[("BLIND", "盲盘"), ("VERIFY", "明盘")],default="BLIND",)
    countorder=models.CharField( "盘点次序", max_length=10, choices=CountOrder.choices,default=CountOrder.FIRST)

    class Meta:
        verbose_name = "盘点扩展"
        verbose_name_plural = "盘点扩展"
        constraints = [
            # line 是 OneToOne，无需再加唯一约束
            models.CheckConstraint(check=Q(qty_counted__gte=0), name="ck_cnt_qc_ge0"),
            models.CheckConstraint(check=Q(qty_book__gte=0),    name="ck_cnt_qb_ge0"),
            # models.CheckConstraint(
            #     name="ck_cnt_diff_eq_c_minus_b",
            #     check=Q(qty_diff=F("qty_counted") - F("qty_book")),
            # ),
            models.CheckConstraint(
                name="ck_cnt_diff_eq_c_minus_b",
                check=Q(
                    qty_diff=ExpressionWrapper(
                        F("qty_counted") - F("qty_book"),
                        output_field=DecimalField(max_digits=18, decimal_places=3),  # 按你的字段精度
                    )
                ),
            ),
        ]
        indexes = [
            models.Index(fields=["lot_no"],  name="ix_cnt_lot"),
            models.Index(fields=["exp_date"], name="ix_cnt_exp"),
            models.Index(fields=["lpn_no"],  name="ix_cnt_lpn"),
        ]

    def _get_task_status(self):
        line = getattr(self, "line", None)
        return get_task_status_via_line(line)

    @classmethod
    def expected_task_type(cls):
        return "COUNT"


    def clean(self):
        super().clean()
        # 若需要：校验库位属于任务仓库
        if self.line_id and getattr(self.line, "from_location_id", None):
            loc_wh = getattr(self.line.from_location, "warehouse_id", None)
            if loc_wh and loc_wh != self.line.task.warehouse_id:
                raise ValidationError({"__all__": "盘点库位不属于该任务仓库"})
        c = self.qty_counted or Decimal("0")
        b = self.qty_book    or Decimal("0")
        print("0 self.qty_diff= c,b " ,self.qty_diff,c,b)
        # 精度按你的字段 decimal_places 量化（假设 3 位）
        self.qty_diff = (c - b).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        print("1 self.qty_diff=" ,self.qty_diff)
        if self.qty_counted>0:
            self.count_status="COUNTED"

    @transaction.atomic
    def save(self, *args, **kwargs):
        print("*********************Save method called!")  # 检查是否在页面加载时调用
        by_user = kwargs.pop("by_user", None)   # 调用方传入 request.user

        if self.lot_no:
            self.lot_no = self.lot_no.strip().upper()
        def _to_dec(x):
            return x if isinstance(x, Decimal) else Decimal(x or 0)

        update_fields = kwargs.get("update_fields")

        if  _to_dec(self.qty_counted) > 0:
            self.count_status = "COUNTED"
            if update_fields is not None:
                update_fields = set(update_fields)
                update_fields.add("count_status")
                kwargs["update_fields"] = list(update_fields)

        self.full_clean()  # 确保先进行数据验证
        return super().save(*args, **kwargs)

        # countorder=self.countorder
        # line = getattr(self, "line", None)
        # task = getattr(line, "task", None) if line else None
        #
        # print("0 COUNTALINEEXTRA SAVE if task:")
        # if task:
        #     print("1 COUNTALINEEXTRA SAVE task is true:")
        #     line_ids = task.lines.values_list("id", flat=True)
        #     print("0 pending_exists::")
        #     pending_exists = (
        #         self.__class__.objects
        #         .filter(line_id__in=line_ids)
        #         .exclude(count_status="COUNTED")
        #         .exists()
        #     )
        #     if pending_exists:
        #         return ret
        #
        #     print("1 pending_exists::")
        #
        #     WmsTask.objects.filter(pk=task.pk).update(
        #         status=WmsTask.Status.COMPLETED,
        #         review_status=WmsTask.ReviewStatus.APPROVED,
        #         posting_status=WmsTask.PostingStatus.PENDING,
        #     )
        #     print("WmsTask.objects.filter(pk=task.pk).update 1 not pending_exists::")
        #     ORDER = ("FIRST", "SECOND", "THIRD")  # 现在只用到三次
        #     limit = max(1, min(getattr(settings, "COUNT_MAX_TIMES", 3), len(ORDER)))
        #
        #     i = ORDER.index(countorder)  # 快速失败：写错枚举就直接抛错，便于调试
        #     if i + 1 >= limit:
        #         return ret
        #
        #     newcountorder = ORDER[i + 1]
        #
        #     diff_qs = (
        #         self.__class__.objects
        #         .filter(line_id__in=line_ids)
        #         .exclude(qty_diff=0)
        #         .exclude(qty_diff__isnull=True)
        #     )
        #     if not diff_qs.exists():
        #         return ret
        #
        #     biz_date=datetime.today().date()
        #     count_task_no = DocSequence.next_code(
        #         doc_type="PD",
        #         warehouse=task.warehouse,
        #         owner=task.owner,
        #         biz_date=biz_date,
        #     )
        #     remark = f"任务号{task.task_no}的复盘"
        #     rc = WmsTask.objects.create(
        #         task_no=count_task_no,
        #         task_type=WmsTask.TaskType.COUNT,
        #         status=WmsTask.Status.DRAFT,
        #         owner=task.owner,
        #         warehouse=task.warehouse,
        #         remark=remark,
        #         created_by=by_user,
        #     )
        #
        #     created = 0
        #     for diffrecord in diff_qs:
        #         line = WmsTaskLine.objects.create(
        #             task=rc,
        #             product=diffrecord.line.product,
        #             from_location=diffrecord.line.from_location,
        #             qty_plan= diffrecord.qty_book,
        #             status=WmsTaskLine.Status.DRAFT,
        #         )
        #         CountLineExtra.objects.create(
        #             line=line,
        #             lot_no=diffrecord.lot_no,
        #             exp_date=diffrecord.exp_date,
        #
        #             qty_book=diffrecord.qty_book,
        #             qty_counted=Decimal("0"),
        #             method="BLIND",
        #             countorder=newcountorder,
        #         )
        #         created += 1
        #         print("created CountLineExtra",created)
        # print("1 if task:")
        # return ret

# == 质检头 ==
class QCTaskExtra(TaskExtraBase):
    """质检头：策略/默认参数"""
    QC_POLICY = [("ALL", "全检"), ("RANDOM", "抽检"), ("NONE", "不检")]

    policy = models.CharField("质检策略", max_length=10, choices=QC_POLICY, default="RANDOM", db_index=True)
    sample_ratio = models.DecimalField("抽检比例(0~1]", max_digits=4, decimal_places=3, null=True, blank=True)
    quarantine_zone = models.CharField("隔离区域", max_length=20, blank=True, default="")
    require_photo = models.BooleanField("需拍照留存", default=False)

    class Meta:
        verbose_name = "质检任务头扩展"
        verbose_name_plural = "质检任务头扩展"
        constraints = [
            # OneToOne 已唯一；此约束可删
            # models.UniqueConstraint(fields=["task"], name="ux_qctsk_task"),
            # RANDOM 时 0<ratio<=1；非 RANDOM 时不强制
            models.CheckConstraint(
                name="ck_qc_ratio",
                check=(~Q(policy="RANDOM")) | (Q(sample_ratio__gt=0) & Q(sample_ratio__lte=1)),
            ),
        ]
        indexes = [
            models.Index(fields=["policy"], name="ix_qctsk_pol"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "QC"

    def clean(self):
        super().clean()
        if self.policy == "RANDOM":
            if self.sample_ratio is None or not (Decimal("0") < self.sample_ratio <= Decimal("1")):
                raise ValidationError({"sample_ratio": "抽检策略下，抽检比例必须在 (0, 1]。"})
# == 质检行 ==
class QCLineExtra(TaskLineExtraBase):
    """质检行：检验结果与数量、去向等"""
    RESULT = [("", "未判定"), ("PASS", "合格"), ("REJECT", "不合格"), ("REWORK", "返工")]

    result = models.CharField("结果", max_length=10, choices=RESULT, blank=True, default="", db_index=True)
    qty_checked = models.DecimalField("检验数", max_digits=14, decimal_places=4, default=Decimal("0"))
    qty_pass = models.DecimalField("合格数", max_digits=14, decimal_places=4, default=Decimal("0"))
    qty_reject = models.DecimalField("不合格数", max_digits=14, decimal_places=4, default=Decimal("0"))
    reason_code = models.CharField("不合格原因", max_length=20, blank=True, default="")
    to_quarantine_loc = models.ForeignKey(
        "locations.Location", on_delete=models.PROTECT, null=True, blank=True, verbose_name="隔离库位"
    )

    class Meta:
        verbose_name = "质检行扩展"
        verbose_name_plural = "质检行扩展"
        constraints = [
            # line 是 OneToOne，天然唯一；可去掉
            # models.UniqueConstraint(fields=["line"], name="ux_qc_extra_line"),
            models.CheckConstraint(
                name="ck_qc_num_ge0",
                check=Q(qty_checked__gte=0) & Q(qty_pass__gte=0) & Q(qty_reject__gte=0),
            ),
            # 若希望严格等式可用以下约束（MySQL 8.0.16+ 生效）：
            models.CheckConstraint(
                name="ck_qc_eq_checked_sum",
                check=Q(qty_checked=F("qty_pass") + F("qty_reject")),
            ),
        ]
        indexes = [
            models.Index(fields=["result"], name="ix_qcline_result"),
            models.Index(fields=["to_quarantine_loc"], name="ix_qcline_quar_loc"),
        ]

    @classmethod
    def expected_task_type(cls):
        return "QC"

    def clean(self):
        super().clean()

        # 1) 等式/边界的人类可读校验（与 DB 约束一致）
        if (self.qty_checked or Decimal("0")) != (self.qty_pass or Decimal("0")) + (self.qty_reject or Decimal("0")):
            raise ValidationError({"qty_checked": "检验数必须等于 合格数 + 不合格数。"})

        # 2) 不合格/返工需给原因（按需放宽）
        if (self.result in ("REJECT", "REWORK") or (self.qty_reject or 0) > 0) and not self.reason_code.strip():
            raise ValidationError({"reason_code": "存在不合格数量或结果为不合格/返工时，必须填写原因。"})

        # 3) 隔离库位必须在任务仓库
        if self.to_quarantine_loc_id and self.line_id:
            task_wh_id = self.line.task.warehouse_id
            if self.to_quarantine_loc.warehouse_id != task_wh_id:
                raise ValidationError({"to_quarantine_loc": "隔离库位不在该任务仓库。"})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

# ==库存调整(含报损) 任务头==
class AdjustTaskExtra(TaskExtraBase):
    ADJ_MODE = [("GENERAL", "一般调整"), ("SCRAP", "报损")]
    ADJ_SCAN_POLICY = [("NONE", "不扫码"), ("LOC", "扫库位"), ("LOC_LPN", "库位+LPN"), ("DOUBLE", "双人复核")]

    # 统一处置枚举（头/行共用）
    class DisposalChoices(models.TextChoices):
        NONE = "", "（未指定）"
        SCRAP = "SCRAP", "报废"
        DESTROY = "DESTROY", "销毁"
        RETURN = "RETURN", "退供应商"
        REWORK = "REWORK", "返工"
        DONATE = "DONATE", "捐赠"
        RECYCLE = "RECYCLE", "回收"

    mode = models.CharField("模式", max_length=10, choices=ADJ_MODE, default="GENERAL", db_index=True)
    scan_policy = models.CharField("扫码策略", max_length=10, choices=ADJ_SCAN_POLICY, default="LOC_LPN", db_index=True)
    require_photo = models.BooleanField("需拍照", default=False)
    default_reason_code = models.CharField("默认原因码", max_length=20, blank=True, default="")
    # 与行枚举一致
    default_disposal_code = models.CharField(
        "默认处置方式",
        max_length=10,
        choices=DisposalChoices.choices,
        default=DisposalChoices.NONE,
        blank=True,
    )

    class Meta:
        verbose_name = "库存调整任务头扩展"
        verbose_name_plural = "库存调整任务头扩展"
        constraints = [
            # OneToOne 天然唯一；这条可删
            # models.UniqueConstraint(fields=["task"], name="ux_adjtsk_task"),
        ]
        indexes = [
            models.Index(fields=["mode"], name="ix_adjtsk_mode"),
            models.Index(fields=["scan_policy"], name="ix_adjtsk_scan"),
        ]

    @classmethod
    def expected_task_type(cls): return "ADJUST"
# ==库存调整行==
class AdjustLineExtra(TaskLineExtraBase):

    """库存调整行(含报损)：定位 + 数量 + 原因/处置快照"""

    # 统一处置枚举（头/行共用）
    class DisposalChoices(models.TextChoices):
        NONE = "", "（未指定）"
        SCRAP = "SCRAP", "报废"
        DESTROY = "DESTROY", "销毁"
        RETURN = "RETURN", "退供应商"
        REWORK = "REWORK", "返工"
        DONATE = "DONATE", "捐赠"
        RECYCLE = "RECYCLE", "回收"

    location  = models.ForeignKey("locations.Location", on_delete=models.PROTECT,
                                  null=True, blank=True, verbose_name="库位")
    lpn_code  = models.CharField("LPN", max_length=40, blank=True, default="", db_index=True)
    delta_qty = models.DecimalField("调整数量(+增/-减)", max_digits=14, decimal_places=4)

    reason_code   = models.CharField("原因码(快照)", max_length=20, blank=True, default="")
    disposal_code = models.CharField(
        "处置方式(快照)",
        max_length=10,
        choices=DisposalChoices.choices,
        default=DisposalChoices.NONE,
        blank=True,
    )
    need_photo = models.BooleanField("需照片(快照)", default=False)
    checked = models.BooleanField("已复核", default=False, db_index=True)
    checked_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT,
        null=True, blank=True, verbose_name="复核人"
    )
    checked_at = models.DateTimeField("复核时间", null=True, blank=True)

    class Meta:
        verbose_name = "库存调整行扩展"
        verbose_name_plural = "库存调整行扩展"
        constraints = [
            # OneToOne 天然唯一；这条可删
            # models.UniqueConstraint(fields=["line"], name="ux_adj_extra_line"),
            # 调整数量不可为 0
            models.CheckConstraint(name="ck_adj_delta_ne0", check=~Q(delta_qty=0)),
            # 若填了处置方式(非空)，数量必须为负（报损类）
            models.CheckConstraint(
                name="ck_adj_scrap_neg",
                check=Q(disposal_code="") | Q(delta_qty__lt=0),
            ),
            # 至少定位到库位或 LPN 之一
            models.CheckConstraint(
                name="ck_adj_loc_or_lpn",
                check=Q(location__isnull=False) | ~Q(lpn_code=""),
            ),
        ]
        indexes = [
            models.Index(fields=["location"], name="ix_adj_loc"),
            models.Index(fields=["lpn_code"], name="ix_adj_lpn"),
            models.Index(fields=["reason_code"], name="ix_adj_rsn"),
            models.Index(fields=["disposal_code"], name="ix_adj_dsp"),
        ]

    @classmethod
    def expected_task_type(cls): return "ADJUST"

    def clean(self):
        super().clean()

        # 1) 仓库一致性：填写了库位时，必须属于任务仓库
        if self.line_id and self.location_id:
            task_wh_id = self.line.task.warehouse_id
            if self.location.warehouse_id != task_wh_id:
                raise ValidationError({"location": "库位不在该任务仓库。"})

        # 2) 报损/处置类：建议强制原因码
        if self.disposal_code and not self.reason_code.strip():
            raise ValidationError({"reason_code": "选择了处置方式时必须填写原因码。"})

        # 3) 业务容错：若选择处置且数量为正，虽然有 DB CHECK，但在应用层给出更友好的错误
        if self.disposal_code and (self.delta_qty or Decimal("0")) > 0:
            raise ValidationError({"delta_qty": "处置类调整（报损/返工等）必须为负数。"})

    def save(self, *args, **kwargs):
        # 归一化 LPN
        if self.lpn_code:
            self.lpn_code = self.lpn_code.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)

#容器使用
class ContainerUsage(BaseModel):
    PURPOSE = [("OUTBOUND","出库"), ("INBOUND","入库"), ("MOVE","移库"), ("COUNT","盘点")]
    STATUS  = [("OPEN","进行中"), ("CLOSED","已结束")]
    QC      = [("PENDING","待复核"), ("PASSED","通过"), ("FAILED","未过")]

    container = models.ForeignKey("locations.Container", on_delete=models.PROTECT, related_name="usages", verbose_name="容器")
    task = models.ForeignKey("tasking.WmsTask", on_delete=models.PROTECT, related_name="container_usages", verbose_name="任务")
    purpose = models.CharField("用途", max_length=10, choices=PURPOSE, db_index=True, default="OUTBOUND")
    status = models.CharField("使用状态", max_length=8, choices=STATUS, db_index=True, default="OPEN")

    qc_status = models.CharField("复核状态", max_length=10, choices=QC, default="PENDING", db_index=True)
    qc_by  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                               related_name="container_usages_qc_by", verbose_name="复核人")
    qc_at  = models.DateTimeField("复核时间", null=True, blank=True)
    qc_note = models.CharField("复核备注", max_length=120, blank=True, default="")
    on_hold = models.BooleanField("拦截发运", default=False)

    gross_weight_kg = models.DecimalField("毛重(kg)", max_digits=10, decimal_places=3, null=True, blank=True)
    carrier_code  = models.CharField("承运商", max_length=20, blank=True, default="")
    service_level = models.CharField("服务级别", max_length=20, blank=True, default="")
    tracking_no   = models.CharField("运单号", max_length=50, blank=True, default="", db_index=True)

    closed_at = models.DateTimeField("结束时间", null=True, blank=True)
    memo = models.CharField("备注", max_length=120, blank=True, default="")

    class Meta:
        verbose_name = "容器使用"
        verbose_name_plural = "容器使用"
        constraints = [
            # 同一任务+容器+用途唯一
            models.UniqueConstraint(fields=["task", "container", "purpose"], name="ux_usage_task_container_purpose"),

            # 同一容器+用途下，最多一条 OPEN（表达式唯一；MySQL 8 需确认支持）
            models.UniqueConstraint(
                F("container"),
                F("purpose"),
                Case(When(status="OPEN", then=Value(1)), default=Value(None), output_field=IntegerField()),
                name="ux_usage_cont_purp_open",
            ),

            # 状态-时间成对
            models.CheckConstraint(
                name="ck_usage_open_no_closed_at",
                check=Q(status="OPEN", closed_at__isnull=True) | ~Q(status="OPEN"),
            ),
            models.CheckConstraint(
                name="ck_usage_closed_has_closed_at",
                check=Q(status="CLOSED", closed_at__isnull=False) | ~Q(status="CLOSED"),
            ),

            # 毛重非负
            models.CheckConstraint(
                check=Q(gross_weight_kg__isnull=True) | Q(gross_weight_kg__gte=0),
                name="ck_usage_gw_nonneg",
            ),
        ]
        indexes = [
            models.Index(fields=["container", "purpose", "status"], name="ix_usage_cont_purpose_status"),
            models.Index(fields=["task", "purpose"], name="ix_usage_task_purpose"),
            models.Index(fields=["carrier_code", "tracking_no"], name="ix_usage_carrier_track"),
        ]

    def clean(self):
        super().clean()
        # 仓库一致
        if getattr(self.task, "warehouse_id", None) and getattr(self.container, "warehouse_id", None):
            if self.task.warehouse_id != self.container.warehouse_id:
                raise ValidationError({"task": "任务仓库与容器所属仓库不一致"})

        # 公共容器时不强制 owner 一致
        cont_owner = getattr(self.container, "owner_id", None)
        if cont_owner and getattr(self, "owner_id", None) and self.owner_id != cont_owner:
            raise ValidationError({"owner": "Usage.owner 必须与 Container.owner 一致"})

        # 承重校验（如定义了容器额定/皮重）
        if self.gross_weight_kg is not None:
            gw = Decimal(self.gross_weight_kg)
            if gw < 0:
                raise ValidationError({"gross_weight_kg": "毛重不能为负"})
            tare = getattr(self.container, "tare_weight_kg", None)
            payload = getattr(self.container, "rated_payload_kg", None)
            if tare is not None and payload is not None:
                limit = Decimal(tare) + Decimal(payload)
                if gw > limit:
                    raise ValidationError({"gross_weight_kg": "毛重超过容器额定承重上限"})
            if getattr(self.container, "rated_payload_kg", None) is not None and getattr(self.container, "tare_weight_kg", None) is not None:
                if gw - Decimal(self.container.tare_weight_kg) > Decimal(self.container.rated_payload_kg):
                    raise ValidationError({"gross_weight_kg": "净载超过容器额定载重上限"})

    def save(self, *args, **kwargs):
        # 同步 owner/warehouse（若 BaseModel 带这两个字段）
        if hasattr(self, "owner_id") and not self.owner_id:
            self.owner_id = getattr(self.container, "owner_id", None)
        if hasattr(self, "warehouse_id") and not self.warehouse_id:
            self.warehouse_id = getattr(self.container, "warehouse_id", None)

        # 归一化编码
        if self.carrier_code:
            self.carrier_code = self.carrier_code.strip().upper()
        if self.service_level:
            self.service_level = self.service_level.strip().upper()
        if self.tracking_no:
            self.tracking_no = self.tracking_no.strip()

        # 自动回填 closed_at
        if self.status == "CLOSED" and not self.closed_at:
            self.closed_at = timezone.now()

        self.full_clean()
        return super().save(*args, **kwargs)










