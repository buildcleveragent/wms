# allapp/outbound/management/commands/release_to_pick.py
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from allapp.outbound.models import OutboundOrder  # 对齐路径
from allapp.outbound import services as ob_services


class Command(BaseCommand):
    help = "将指定出库单释放为拣货任务（分配 FEFO 并冻结可用量）"

    def add_arguments(self, parser):
        parser.add_argument("--order", type=int, help="出库单 ID")
        parser.add_argument("--order-no", type=str, help="出库单号（如存在 order_no 字段）")
        parser.add_argument("--no-backorder", action="store_true", help="库存不足即报错，不允许回欠")

    def handle(self, *args, **opts):
        order_id = opts.get("order")
        order_no = opts.get("order_no")
        allow_backorder = not opts.get("no_backorder")

        if not order_id and not order_no:
            raise CommandError("必须提供 --order 或 --order-no 其中之一。")

        if order_id:
            try:
                order = OutboundOrder.objects.select_related("owner", "warehouse").get(pk=order_id)
            except OutboundOrder.DoesNotExist:
                raise CommandError(f"未找到出库单 ID={order_id}")
        else:
            # 如你的编号字段不是 order_no，请替换为真实字段名
            try:
                order = OutboundOrder.objects.select_related("owner", "warehouse").get(order_no=order_no)
            except OutboundOrder.DoesNotExist:
                raise CommandError(f"未找到出库单号={order_no}")

        task = ob_services.release_to_pick(order, by_user=None, allow_backorder=allow_backorder)
        self.stdout.write(self.style.SUCCESS(f"OK: 生成拣货任务 Task ID={task.id}"))
