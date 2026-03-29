import datetime

from django.core.management.base import BaseCommand, CommandError

from allapp.baseinfo.models import Owner
from allapp.billing.enums import MetricType
from allapp.billing.services import generate_metrics_for_range
from allapp.locations.models import Warehouse


class Command(BaseCommand):
    help = "Generate BillingMetricDaily rows from inventory/outbound source data."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=str, help="YYYY-MM-DD. If omitted, defaults to yesterday.")
        parser.add_argument("--date-from", type=str, dest="date_from", help="YYYY-MM-DD start date.")
        parser.add_argument("--date-to", type=str, dest="date_to", help="YYYY-MM-DD end date.")
        parser.add_argument("--owner", type=int, help="owner_id (optional)")
        parser.add_argument("--warehouse", type=int, help="warehouse_id (optional)")
        parser.add_argument(
            "--metric-type",
            action="append",
            choices=[choice.value for choice in MetricType],
            dest="metric_types",
            help="Repeatable. Limit generation to selected metric types.",
        )
        parser.add_argument("--overwrite", action="store_true", help="Allow overwriting non-auto metric rows.")
        parser.add_argument(
            "--allow-area-fallback",
            action="store_true",
            help="Use occupied location count as AREA_M2 fallback when no explicit area resolver exists.",
        )

    def handle(self, *args, **opts):
        if opts.get("date"):
            start_date = end_date = datetime.date.fromisoformat(opts["date"])
        elif opts.get("date_from") or opts.get("date_to"):
            if not opts.get("date_from") or not opts.get("date_to"):
                raise CommandError("--date-from and --date-to must be provided together.")
            start_date = datetime.date.fromisoformat(opts["date_from"])
            end_date = datetime.date.fromisoformat(opts["date_to"])
        else:
            start_date = end_date = datetime.date.today() - datetime.timedelta(days=1)

        owners = Owner.objects.all()
        warehouses = Warehouse.objects.all()
        if opts.get("owner"):
            owners = owners.filter(id=opts["owner"])
        if opts.get("warehouse"):
            warehouses = warehouses.filter(id=opts["warehouse"])

        total_created = total_updated = total_deleted = 0
        total_skipped_manual = total_unsupported = total_noop = total_skipped_zero = 0

        for owner in owners:
            for warehouse in warehouses:
                summary = generate_metrics_for_range(
                    owner.id,
                    warehouse.id,
                    start_date,
                    end_date,
                    metric_types=opts.get("metric_types"),
                    overwrite=opts.get("overwrite", False),
                    allow_area_fallback=opts.get("allow_area_fallback", False),
                )
                total_created += summary["created"]
                total_updated += summary["updated"]
                total_deleted += summary["deleted_zero"]
                total_skipped_manual += summary["skipped_manual"]
                total_unsupported += summary["unsupported"]
                total_noop += summary["noop"]
                total_skipped_zero += summary["skipped_zero"]

        self.stdout.write(
            self.style.SUCCESS(
                "Billing metrics generated "
                f"for {start_date}..{end_date}: "
                f"created={total_created}, updated={total_updated}, deleted_zero={total_deleted}, "
                f"skipped_zero={total_skipped_zero}, skipped_manual={total_skipped_manual}, "
                f"unsupported={total_unsupported}, noop={total_noop}"
            )
        )
