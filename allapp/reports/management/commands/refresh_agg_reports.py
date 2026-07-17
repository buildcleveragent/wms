# allapp/reports/management/commands/refresh_agg_reports.py
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.reports.aggregation import refresh_daily_aggregates

class Command(BaseCommand):
    help = "刷新报表聚合：吞吐日汇总、计费日汇总。参数：--date YYYY-MM-DD（必传）"

    def add_arguments(self, parser):
        parser.add_argument("--date", required=True)

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            d = datetime.strptime(opts["date"], "%Y-%m-%d").date()
        except Exception as e:
            raise CommandError(f"参数错误: {e}")
        refresh_daily_aggregates([d])

        self.stdout.write(self.style.SUCCESS(f"聚合刷新完成：{d}"))
