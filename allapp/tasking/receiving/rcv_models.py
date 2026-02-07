# allapp/tasking/rcv_models.py
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from allapp.tasking.models import WmsTask, WmsTaskLine, TaskType, TaskStatus
from allapp.inbound.models import InboundOrder  # 如路径不同，请按你项目调整
from allapp.baseinfo.models import Vehicle, CarrierCompany

class RcvTaskManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(task_type=TaskType.RCV)

class RcvTask(WmsTask):
    """收货任务视图：基于通用任务的 proxy，保留收货语义与便捷方法"""
    objects = RcvTaskManager()

    class Meta:
        proxy = True
        verbose_name = _("收货任务")
        verbose_name_plural = _("收货任务")

    # 便捷方法（委托给通用状态机/服务层）
    def start(self, user=None):
        from . import services
        if self.status not in (TaskStatus.RELEASED, TaskStatus.CLAIMED):
            raise ValidationError("仅已发布/已领取的任务可开始")
        services.start_task(self.pk, user)

    def complete(self, user=None):
        from . import services
        if self.status != TaskStatus.IN_PROGRESS:
            raise ValidationError("仅执行中的任务可完成")
        services.change_status(self, TaskStatus.COMPLETED, by_user=user, reason="收货完成")

class RcvTaskExtra(models.Model):
    """收货专属信息（不破坏通用任务表结构）"""
    SOURCE_CHOICES = [("MANUAL", "手工"), ("RETURN", "退货"), ("CROSS_DOCK", "越库"), ("OTHER", "其他")]

    task = models.OneToOneField(
        WmsTask, on_delete=models.CASCADE, related_name="rcv_extra", verbose_name=_("所属任务")
    )
    order = models.ForeignKey(
        InboundOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="receiving_tasks", verbose_name=_("入库订单")
    )
    source_type = models.CharField(_("来源类型"), max_length=20, choices=SOURCE_CHOICES, default="MANUAL")
    source_ref = models.CharField(_("来源参考号"), max_length=60, blank=True, null=True)

    dock_door = models.CharField(_("月台/道口"), max_length=30, blank=True, null=True)
    vehicle_no = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True, related_name="rcv_tasks", verbose_name=_("车牌"))
    carrier_company = models.ForeignKey(CarrierCompany, on_delete=models.PROTECT, null=True, blank=True, related_name="rcv_tasks", verbose_name=_("承运商"))

    class Meta:
        verbose_name = _("收货任务信息")
        verbose_name_plural = _("收货任务信息")
        indexes = [
            models.Index(fields=["order_id"], name="idx_rcvext_order"),
        ]

    def clean(self):
        # 1) 限定任务类型必须是 RCV
        if self.task and self.task.task_type != TaskType.RCV:
            raise ValidationError("RcvTaskExtra 仅可绑定 task_type=RCV 的任务")
        # 2) 有订单时，校验货主/仓库一致
        if self.order_id:
            if self.task.owner_id != self.order.owner_id:
                raise ValidationError("任务与订单货主不一致")
            if self.task.warehouse_id != self.order.warehouse_id:
                raise ValidationError("任务与订单仓库不一致")
