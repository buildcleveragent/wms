from django.core.management.base import BaseCommand

from allapp.inventory.tracking_cleanup import (
    collect_inventory_tracking_gap_rows,
    export_inventory_tracking_gap_rows,
)


class Command(BaseCommand):
    help = (
        "Export current inventory batch/expiry tracking gaps to a CSV template "
        "for manual repair."
    )

    def add_arguments(self, parser):
        parser.add_argument("output_csv", type=str)
        parser.add_argument("--owner", type=int, help="Optional owner_id scope.")
        parser.add_argument("--warehouse", type=int, help="Optional warehouse_id scope.")

    def handle(self, *args, **options):
        rows = collect_inventory_tracking_gap_rows(
            owner_id=options.get("owner"),
            warehouse_id=options.get("warehouse"),
        )
        export_inventory_tracking_gap_rows(rows, options["output_csv"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported inventory tracking repair template: rows={len(rows)} path={options['output_csv']}"
            )
        )
