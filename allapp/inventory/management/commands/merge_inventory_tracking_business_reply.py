from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError

from allapp.inventory.tracking_cleanup import merge_inventory_tracking_business_reply_into_template


def _default_output_path(template_csv: str) -> str:
    template_path = Path(template_csv)
    if template_path.suffix.lower() == ".csv":
        return str(template_path.with_name(f"{template_path.stem}.merged.csv"))
    return f"{template_csv}.merged.csv"


class Command(BaseCommand):
    help = (
        "Merge a business reply CSV back into the inventory tracking repair template."
    )

    def add_arguments(self, parser):
        parser.add_argument("template_csv", type=str)
        parser.add_argument("reply_csv", type=str)
        parser.add_argument("--output", type=str, help="Optional merged output CSV path.")
        parser.add_argument(
            "--in-place",
            action="store_true",
            help="Overwrite the original template CSV in place.",
        )

    def handle(self, *args, **options):
        if options["output"] and options["in_place"]:
            raise CommandError("--output and --in-place cannot be used together.")

        output_file = (
            options["template_csv"]
            if options["in_place"]
            else options["output"] or _default_output_path(options["template_csv"])
        )

        try:
            summary = merge_inventory_tracking_business_reply_into_template(
                options["template_csv"],
                options["reply_csv"],
                output_file,
            )
        except ValidationError as exc:
            raise CommandError(exc) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Merged inventory tracking business reply: "
                f"reply_groups={summary['reply_groups']} "
                f"matched_groups={summary['matched_groups']} "
                f"matched_rows={summary['matched_rows']} "
                f"updated_rows={summary['updated_rows']} "
                f"rows_without_reply={summary['rows_without_reply']} "
                f"path={summary['output_file']}"
            )
        )
