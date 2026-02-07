# allapp/reports/management/commands/refresh_agg_reports.py
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum, Count
from allapp.reports.models import (
    AggThroughputDaily, AggBillingDaily,
    FactOutboundLine, FactBilling, OwnerDim, WarehouseDim
)
from allapp.reports.etl_utils import ensure_datedim

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
        dd = ensure_datedim(d)

        # ---- 吞吐日汇总（按 owner/warehouse）----
        AggThroughputDaily.objects.filter(date=dd).delete()

        # 出库
        ob = (FactOutboundLine.objects
              .filter(order_date=dd)
              .values("owner_id", "warehouse_id")
              .annotate(lines=Count("line_id"), qty=Sum("qty_shipped")))
        # 这里示例只汇总出库；如需入库也做同法聚合后 merge
        for row in ob:
            AggThroughputDaily.objects.update_or_create(
                date=dd,
                owner_id=row["owner_id"],
                warehouse_id=row["warehouse_id"],
                defaults=dict(
                    inbound_lines=0, inbound_qty=0,
                    outbound_lines=row["lines"] or 0,
                    outbound_qty=row["qty"] or 0
                )
            )

        # ---- 计费日汇总（按 owner/warehouse/fee_type）----
        AggBillingDaily.objects.filter(date=dd).delete()
        fb = (FactBilling.objects
              .filter(date=dd)
              .values("owner_id", "warehouse_id", "fee_type")
              .annotate(amount=Sum("amount")))
        for row in fb:
            AggBillingDaily.objects.update_or_create(
                date=dd,
                owner_id=row["owner_id"],
                warehouse_id=row["warehouse_id"],
                fee_type=row["fee_type"],
                defaults=dict(amount=row["amount"] or 0)
            )

        self.stdout.write(self.style.SUCCESS(f"聚合刷新完成：{d}"))
