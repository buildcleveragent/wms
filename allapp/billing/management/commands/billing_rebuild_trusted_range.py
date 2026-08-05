import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.billing.enums import AccrualStatus, SourceQuality
from allapp.billing.models import BillingAccrual
from allapp.billing.services import (
    accrue_metrics_for_date,
    accrue_storage_for_date,
    generate_metrics_for_date,
)
from allapp.inventory.models import InventorySnapshotDaily
from allapp.inventory.snapshot_services import generate_inventory_snapshot_for_date


class Command(BaseCommand):
    help = "Rebuild snapshots, automatic metrics and OPEN storage accruals from a trusted baseline."

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int, required=True)
        parser.add_argument("--warehouse", type=int, required=True)
        parser.add_argument("--date-from", required=True)
        parser.add_argument("--date-to", required=True)
        parser.add_argument(
            "--apply", action="store_true", help="Persist changes; default is preview."
        )

    def handle(self, *args, **options):
        try:
            date_from = datetime.date.fromisoformat(options["date_from"])
            date_to = datetime.date.fromisoformat(options["date_to"])
        except ValueError as exc:
            raise CommandError("Dates must use YYYY-MM-DD.") from exc
        if date_from > date_to:
            raise CommandError("date-from cannot be later than date-to.")
        baseline_date = date_from - datetime.timedelta(days=1)
        baseline = InventorySnapshotDaily.objects.filter(
            owner_id=options["owner"],
            warehouse_id=options["warehouse"],
            snapshot_date=baseline_date,
        )
        if (
            not baseline.exists()
            or baseline.exclude(
                snapshot_source=InventorySnapshotDaily.Source.TX_ROLLFORWARD
            ).exists()
        ):
            raise CommandError(
                "A fully trusted TX_ROLLFORWARD snapshot is required on the preceding day."
            )
        protected = BillingAccrual.objects.filter(
            owner_id=options["owner"],
            warehouse_id=options["warehouse"],
            service_date__range=(date_from, date_to),
            source_quality=SourceQuality.APPROXIMATE,
            status__in=[AccrualStatus.LOCKED, AccrualStatus.INVOICED],
        )
        if protected.exists():
            raise CommandError(
                "Locked/invoiced approximate accruals must be unlocked or reversed first."
            )
        if not options["apply"]:
            self.stdout.write(
                str({"dry_run": True, "date_from": date_from, "date_to": date_to})
            )
            return
        with transaction.atomic():
            BillingAccrual.objects.filter(
                owner_id=options["owner"],
                warehouse_id=options["warehouse"],
                service_date__range=(date_from, date_to),
                status=AccrualStatus.OPEN,
                charge_type="STORAGE",
            ).delete()
            current = date_from
            while current <= date_to:
                InventorySnapshotDaily.objects.filter(
                    owner_id=options["owner"],
                    warehouse_id=options["warehouse"],
                    snapshot_date=current,
                ).delete()
                generate_inventory_snapshot_for_date(
                    current,
                    owner_id=options["owner"],
                    warehouse_id=options["warehouse"],
                    bootstrap=False,
                )
                generate_metrics_for_date(
                    options["owner"],
                    options["warehouse"],
                    current,
                    overwrite=True,
                )
                accrue_storage_for_date(options["owner"], options["warehouse"], current)
                accrue_metrics_for_date(options["owner"], options["warehouse"], current)
                current += datetime.timedelta(days=1)
        self.stdout.write(self.style.SUCCESS("Trusted range rebuilt."))
