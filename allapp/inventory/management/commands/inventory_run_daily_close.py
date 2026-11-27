import logging
import time
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from allapp.inventory.daily_close import run_daily_close
from allapp.inventory.daily_close_schedule import process_due_closes

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "上海时间 00:30 后逐日补齐已启用范围；指定完整日期和范围可手动重检。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="完成一次应结日期扫描后退出")
        parser.add_argument("--date", type=date.fromisoformat)
        parser.add_argument("--owner", type=int)
        parser.add_argument("--warehouse", type=int)

    def handle(self, *args, **options):
        requested = (options["date"], options["owner"], options["warehouse"])
        if any(requested):
            if not all(requested):
                raise CommandError("手动补跑需要同时指定 --date、--owner 和 --warehouse。")
            day, owner, warehouse = requested
            try:
                close = run_daily_close(business_date=day, owner_id=owner, warehouse_id=warehouse)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(
                f"{day} owner={owner} warehouse={warehouse} {close.quality} 发现 {len(close.issues)} 项"
            )
            return
        while True:
            try:
                completed, failed = process_due_closes()
                if completed or failed or options["once"]:
                    self.stdout.write(f"完成 {completed} 个日结日期，失败 {failed} 个范围。")
                if options["once"] and failed:
                    raise CommandError("部分日结失败；再次执行可继续。")
            finally:
                close_old_connections()
            if options["once"]:
                return
            time.sleep(60)
