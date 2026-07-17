from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from allapp.billing.models import BillingAccrual
from allapp.inbound.models import InboundOrder
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder
from allapp.reports.etl_operations import (
    source_reconciliation,
    prune_stale_facts,
    require_reconciliation,
    sync_billing_facts,
    sync_dimensions,
    sync_inbound_facts,
    sync_inventory_snapshot,
    sync_inventory_transactions,
    sync_outbound_facts,
)
from allapp.reports.aggregation import refresh_all_daily_aggregates
from allapp.reports.models import EtlJobRun


class Command(BaseCommand):
    help = "报表库全量装载：维度、库存快照、入出库/库存交易/计费事实"

    def add_arguments(self, parser):
        parser.add_argument("--snapdate", required=True, help="库存日快照日期 YYYY-MM-DD")
        parser.add_argument("--from", dest="dfrom", help="事实范围开始 YYYY-MM-DD")
        parser.add_argument("--to", dest="dto", help="事实范围结束 YYYY-MM-DD")

    def handle(self, *args, **options):
        try:
            snapshot_date = datetime.strptime(options["snapdate"], "%Y-%m-%d").date()
            dfrom = datetime.strptime(options["dfrom"], "%Y-%m-%d").date() if options.get("dfrom") else None
            dto = datetime.strptime(options["dto"], "%Y-%m-%d").date() if options.get("dto") else None
            if bool(dfrom) != bool(dto):
                raise ValueError("--from and --to must be supplied together")
            if dfrom and dto < dfrom:
                raise ValueError("--to must not be before --from")
        except ValueError as exc:
            raise CommandError(f"参数错误: {exc}") from exc

        run = EtlJobRun.objects.create(job_name="etl_full_reports")
        try:
            # Every mart mutation and the success marker commit together. A
            # failed reconciliation therefore leaves the previous published
            # facts untouched, while the run row (created above) remains
            # available to record the failure after rollback.
            with transaction.atomic():
                changed_dims = sync_dimensions()
                inbound_orders = InboundOrder.objects.all()
                outbound_orders = OutboundOrder.objects.all()
                transactions = InventoryTransaction.objects.filter(posted_at__isnull=False)
                accruals = BillingAccrual.objects.all()
                if dfrom:
                    inbound_orders = inbound_orders.filter(biz_date__range=(dfrom, dto))
                    outbound_orders = outbound_orders.filter(biz_date__range=(dfrom, dto))
                    transactions = transactions.filter(posted_at__date__range=(dfrom, dto))
                    accruals = accruals.filter(service_date__range=(dfrom, dto))

                snapshot_rows = sync_inventory_snapshot(snapshot_date)
                inbound_rows = sync_inbound_facts(inbound_orders)
                outbound_rows = sync_outbound_facts(outbound_orders)
                transaction_rows = sync_inventory_transactions(transactions)
                billing_rows = sync_billing_facts(accruals)
                pruned = prune_stale_facts()
                reconciliation = source_reconciliation(dfrom=dfrom, dto=dto)
                require_reconciliation(reconciliation)
                aggregates_refreshed = refresh_all_daily_aggregates()
                rows_out = (
                    snapshot_rows
                    + inbound_rows
                    + outbound_rows
                    + transaction_rows
                    + billing_rows
                )
                rows_in = (
                    inbound_orders.count()
                    + outbound_orders.count()
                    + transactions.count()
                    + accruals.count()
                )
                run.finished_at = timezone.now()
                run.ok = True
                run.rows_in = rows_in
                run.rows_out = rows_out
                run.watermark = timezone.now().isoformat()
                run.reconciliation = {
                    "dimensions_changed": changed_dims,
                    "facts_pruned": pruned,
                    "aggregates_refreshed": aggregates_refreshed,
                    **reconciliation,
                }
                run.save(
                    update_fields=[
                        "finished_at",
                        "ok",
                        "rows_in",
                        "rows_out",
                        "watermark",
                        "reconciliation",
                    ]
                )
        except Exception as exc:
            run.finished_at = timezone.now()
            run.error = str(exc)
            run.save(update_fields=["finished_at", "error"])
            raise CommandError(f"FULL ETL 失败: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"FULL ETL 完成: snapshot={snapshot_rows}, inbound={inbound_rows}, "
                f"outbound={outbound_rows}, transactions={transaction_rows}, billing={billing_rows}"
            )
        )
