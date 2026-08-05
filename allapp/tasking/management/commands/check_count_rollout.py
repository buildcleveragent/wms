from django.core.management.base import BaseCommand, CommandError

from allapp.tasking.models import WmsTask


class Command(BaseCommand):
    help = "上线前检查是否存在无法安全补造冻结范围的历史活动盘点任务。"

    def handle(self, *args, **options):
        active = WmsTask.objects.filter(
            task_type=WmsTask.TaskType.COUNT,
            status__in=[WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS],
        ).order_by("warehouse_id", "task_no")
        rows = list(active.values_list("task_no", "warehouse_id", "status"))
        if rows:
            detail = "，".join(
                f"{task_no}(仓库={warehouse_id}, 状态={status})"
                for task_no, warehouse_id, status in rows
            )
            raise CommandError(
                "发现历史活动盘点任务，请先按现有取消流程关闭并重新创建：" + detail
            )
        self.stdout.write(
            self.style.SUCCESS("盘点上线检查通过：不存在历史活动盘点任务。")
        )
