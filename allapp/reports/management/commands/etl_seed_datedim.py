# allapp/reports/management/commands/etl_seed_datedim.py
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand, CommandError
from allapp.reports.etl_utils import ensure_datedim

class Command(BaseCommand):
    help = "初始化/补齐 DateDim（日期维），参数：--start YYYY-MM-DD --end YYYY-MM-DD"

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True)
        parser.add_argument("--end", required=True)

    def handle(self, *args, **opts):
        try:
            d0 = datetime.strptime(opts["start"], "%Y-%m-%d").date()
            d1 = datetime.strptime(opts["end"], "%Y-%m-%d").date()
            if d1 < d0:
                raise ValueError("end < start")
        except Exception as e:
            raise CommandError(f"参数错误: {e}")

        cnt = 0
        cur = d0
        while cur <= d1:
            ensure_datedim(cur)
            cnt += 1
            cur += timedelta(days=1)
        self.stdout.write(self.style.SUCCESS(f"DateDim OK: {cnt} days"))
