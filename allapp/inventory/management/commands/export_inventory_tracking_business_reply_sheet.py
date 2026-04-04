from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError

from allapp.inventory.tracking_cleanup import export_inventory_tracking_business_reply_sheet


class Command(BaseCommand):
    help = (
        "Export a business-facing reply sheet from an inventory tracking repair template CSV."
    )

    def add_arguments(self, parser):
        parser.add_argument("template_csv", type=str)
        parser.add_argument("output_csv", type=str)

    def handle(self, *args, **options):
        try:
            summary = export_inventory_tracking_business_reply_sheet(
                options["template_csv"],
                options["output_csv"],
            )
        except ValidationError as exc:
            raise CommandError(exc) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Exported inventory tracking business reply sheet: "
                f"template_rows={summary['template_rows']} "
                f"reply_rows={summary['reply_rows']} "
                f"path={options['output_csv']}"
            )
        )
