import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from allapp.billing.services.daily_metric_audit import audit_daily_metrics


class Command(BaseCommand):
    help = "只读核对指定日期范围内待关账的日指标，优先列出曾执行单项的货主、仓库和日期。"

    def add_arguments(self, parser):
        parser.add_argument("--date-from", required=True, help="开始业务日期 YYYY-MM-DD")
        parser.add_argument("--date-to", required=True, help="结束业务日期 YYYY-MM-DD")
        parser.add_argument("--owner", type=int, help="货主 ID")
        parser.add_argument("--warehouse", type=int, help="仓库 ID")

    def handle(self, *args, **options):
        try:
            date_from = date.fromisoformat(options["date_from"])
            date_to = date.fromisoformat(options["date_to"])
        except ValueError as exc:
            raise CommandError("日期请使用 YYYY-MM-DD 格式。") from exc
        if date_to < date_from:
            raise CommandError("结束日期须大于或等于开始日期。")
        report = audit_daily_metrics(
            date_from, date_to, owner_id=options["owner"], warehouse_id=options["warehouse"]
        )
        self.stdout.write(json.dumps(report, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2))
