from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from allapp.billing.models import BillingAccrual
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.reports.etl_operations import (
    root_order_ids_for_tasks,
    prune_stale_facts,
    require_reconciliation,
    source_reconciliation,
    sync_billing_facts,
    sync_dimensions,
    sync_inbound_facts,
    sync_inventory_transactions,
    sync_outbound_facts,
)
from allapp.reports.aggregation import refresh_all_daily_aggregates
from allapp.reports.etl_utils import get_watermark, set_watermark
from allapp.reports.models import EtlJobRun
from allapp.tasking.models import TaskStatusLog, WmsTask, WmsTaskLine


class Command(BaseCommand):
    help = "报表库增量装载：按水位更新受影响的入出库、库存交易与计费事实"

    def add_arguments(self, parser):
        parser.add_argument("--since", help="覆盖起点 ISO 时间")

    def handle(self, *args, **options):
        raw_since = options.get("since") or get_watermark(
            "operations", "1970-01-01T00:00:00+00:00"
        )
        try:
            since = datetime.fromisoformat(raw_since)
            if settings.USE_TZ and timezone.is_naive(since):
                since = timezone.make_aware(since, timezone.get_current_timezone())
            elif not settings.USE_TZ and timezone.is_aware(since):
                since = timezone.make_naive(since, timezone.get_current_timezone())
        except (TypeError, ValueError) as exc:
            raise CommandError("--since 必须是 ISO 日期时间") from exc

        started = timezone.now()
        run = EtlJobRun.objects.create(
            job_name="etl_incremental_reports", watermark=since.isoformat()
        )
        try:
            with transaction.atomic():
                changed_dims = sync_dimensions()
                changed_tasks = WmsTask.objects.filter(updated_at__gt=since).values_list(
                    "id", flat=True
                )
                changed_task_lines = WmsTaskLine.objects.filter(
                    updated_at__gt=since
                ).values_list("task_id", flat=True)
                changed_status_tasks = TaskStatusLog.objects.filter(
                    changed_at__gt=since
                ).values_list("task_id", flat=True)
                changed_tx = InventoryTransaction.objects.filter(
                    Q(updated_at__gt=since) | Q(posted_at__gt=since)
                )
                task_ids_from_tx = changed_tx.filter(
                    src_model__iexact="WmsTask"
                ).values_list("src_id", flat=True)
                all_task_ids = (
                    set(changed_tasks)
                    | set(changed_task_lines)
                    | set(changed_status_tasks)
                    | set(task_ids_from_tx)
                )

                inbound_ids = set(
                    InboundOrder.objects.filter(updated_at__gt=since).values_list(
                        "id", flat=True
                    )
                )
                inbound_ids.update(
                    InboundOrderLine.objects.filter(updated_at__gt=since).values_list(
                        "order_id", flat=True
                    )
                )
                inbound_ids.update(root_order_ids_for_tasks(all_task_ids, InboundOrder))

                outbound_ids = set(
                    OutboundOrder.objects.filter(updated_at__gt=since).values_list(
                        "id", flat=True
                    )
                )
                outbound_ids.update(
                    OutboundOrderLine.objects.filter(updated_at__gt=since).values_list(
                        "order_id", flat=True
                    )
                )
                outbound_ids.update(root_order_ids_for_tasks(all_task_ids, OutboundOrder))

                # BillingAccrual has no updated_at. Re-scan it so void/reversal
                # changes cannot remain stale behind the watermark.
                changed_accruals = BillingAccrual.objects.all()
                inbound_rows = sync_inbound_facts(
                    InboundOrder.objects.filter(id__in=inbound_ids)
                )
                outbound_rows = sync_outbound_facts(
                    OutboundOrder.objects.filter(id__in=outbound_ids)
                )
                transaction_rows = sync_inventory_transactions(changed_tx)
                billing_rows = sync_billing_facts(changed_accruals)
                pruned = prune_stale_facts()
                reconciliation = source_reconciliation()
                require_reconciliation(reconciliation)
                aggregates_refreshed = refresh_all_daily_aggregates()
                watermark = started.isoformat()

                run.finished_at = timezone.now()
                run.ok = True
                run.rows_in = (
                    len(inbound_ids)
                    + len(outbound_ids)
                    + changed_tx.count()
                    + changed_accruals.count()
                )
                run.rows_out = (
                    inbound_rows + outbound_rows + transaction_rows + billing_rows
                )
                run.watermark = watermark
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
                set_watermark("operations", watermark)
        except Exception as exc:
            run.finished_at = timezone.now()
            run.error = str(exc)
            run.save(update_fields=["finished_at", "error"])
            raise CommandError(f"INCR ETL 失败: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"INCR ETL 完成: inbound={inbound_rows}, outbound={outbound_rows}, "
                f"transactions={transaction_rows}, billing={billing_rows}"
            )
        )
