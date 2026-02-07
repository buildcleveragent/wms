# allapp/labeling/models.py
from django.db import models
from django.db.models import Q, CheckConstraint, UniqueConstraint

class Printer(models.Model):
    """
    打印机注册（网络/本地），给服务器侧队列使用
    """
    name = models.CharField("打印机名", max_length=60, unique=True)
    model = models.CharField("型号", max_length=60, blank=True, default="")
    address = models.CharField("地址/队列", max_length=120)  # IP:port 或 cups 队列名
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "打印机"
        verbose_name_plural = "打印机"
        indexes = [
            models.Index(fields=["is_active"], name="idx_printer_active"),
        ]

class LabelTemplate(models.Model):
    """
    标签模板（Jinja2 渲染）；变量在 sample_vars 里示例
    """
    TYPES = [
        ("LPN", "容器/托盘号"),
        ("CASE", "箱唛"),
        ("LOCATION", "库位"),
        ("ITEM", "商品"),
        ("SHIP", "发运"),
    ]
    name = models.CharField("模板名", max_length=80, unique=True)
    type = models.CharField("类型", max_length=12, choices=TYPES)
    engine = models.CharField("引擎", max_length=20, default="jinja2")
    content = models.TextField("模板内容")  # e.g. ZPL/EPL/JSTPL
    sample_vars = models.JSONField("示例变量", default=dict)

    width_mm = models.DecimalField("宽(mm)", max_digits=6, decimal_places=2, default=50)
    height_mm = models.DecimalField("高(mm)", max_digits=6, decimal_places=2, default=30)

    class Meta:
        verbose_name = "标签模板"
        verbose_name_plural = "标签模板"
        constraints = [
            CheckConstraint(check=Q(width_mm__gt=0) & Q(height_mm__gt=0), name="chk_tpl_size_gt0"),
        ]
        indexes = [
            models.Index(fields=["type"], name="idx_tpl_type"),
        ]

class PrintJob(models.Model):
    """
    打印任务（渲染→排队→打印），与业务弱耦合：payload 装变量
    """
    STATUS = [("PENDING", "待渲染"), ("RENDERED", "已渲染"), ("PRINTED", "已打印"), ("FAILED", "失败")]

    owner = models.ForeignKey("baseinfo.Owner", on_delete=models.PROTECT, related_name="print_jobs")
    warehouse = models.ForeignKey("locations.Warehouse", on_delete=models.PROTECT, related_name="print_jobs", null=True, blank=True)
    template = models.ForeignKey(LabelTemplate, on_delete=models.PROTECT, related_name="print_jobs")
    printer = models.ForeignKey(Printer, on_delete=models.PROTECT, related_name="print_jobs", null=True, blank=True)

    status = models.CharField(max_length=12, choices=STATUS, default="PENDING")
    copies = models.PositiveSmallIntegerField("份数", default=1)
    payload = models.JSONField("变量", default=dict)       # { "lpn":"...", "sku":"...", ... }
    rendered = models.TextField("渲染结果", blank=True, default="")  # 保存渲染后的ZPL/EPL
    error = models.CharField("错误", max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "打印任务"
        verbose_name_plural = "打印任务"
        constraints = [
            CheckConstraint(check=Q(copies__gte=1), name="chk_job_copies_ge1"),
        ]
        indexes = [
            models.Index(fields=["owner", "status"], name="idx_job_owner_st"),
            models.Index(fields=["template", "status"], name="idx_job_tpl_st"),
        ]

class LabelRenderLog(models.Model):
    job = models.ForeignKey(PrintJob, on_delete=models.CASCADE, related_name="render_logs")
    ok = models.BooleanField(default=True)
    message = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "渲染日志"
        verbose_name_plural = "渲染日志"
        indexes = [
            models.Index(fields=["job", "created_at"], name="idx_rlog_job_time"),
        ]
