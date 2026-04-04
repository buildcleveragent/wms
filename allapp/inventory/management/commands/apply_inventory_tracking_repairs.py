from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError

from allapp.inventory.tracking_cleanup import apply_inventory_tracking_repairs_from_csv


class Command(BaseCommand):
    help = (
        "Apply inventory batch/expiry tracking repairs from a CSV file exported "
        "by export_inventory_tracking_repair_template."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str)

    def handle(self, *args, **options):
        try:
            summary = apply_inventory_tracking_repairs_from_csv(options["csv_file"])
        except ValidationError as exc:
            raise CommandError(exc) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied inventory tracking repairs: rows={summary['rows']} "
                f"updated={summary['updated']} skipped={summary['skipped']}"
            )
        )
