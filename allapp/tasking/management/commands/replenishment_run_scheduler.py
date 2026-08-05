import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from allapp.tasking.replenishment import evaluate_policies


class Command(BaseCommand):
    help = "Evaluate enabled replenishment policies once or continuously."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--warehouse-id", type=int)
        parser.add_argument("--owner-id", type=int)
        parser.add_argument("--product-id", type=int)

    def _run_once(self, options):
        results = evaluate_policies(
            warehouse_id=options.get("warehouse_id"),
            owner_id=options.get("owner_id"),
            product_id=options.get("product_id"),
        )
        created = sum(1 for row in results if row["created"])
        blocked = sum(1 for row in results if row["reason"] == "NO_SOURCE_STOCK")
        self.stdout.write(
            self.style.SUCCESS(
                f"补货策略评估完成：策略={len(results)}，生成任务={created}，来源不足={blocked}"
            )
        )

    def handle(self, *args, **options):
        if not settings.REPLENISHMENT_MINMAX_ENABLED:
            raise CommandError(
                "阈值补货功能未启用；请设置 REPLENISHMENT_MINMAX_ENABLED=True。"
            )
        interval = options["interval"]
        if interval <= 0:
            raise CommandError("--interval 必须大于零。")
        if options["once"]:
            self._run_once(options)
            return
        self.stdout.write(f"补货调度器已启动，间隔 {interval} 秒。")
        while True:
            self._run_once(options)
            time.sleep(interval)
