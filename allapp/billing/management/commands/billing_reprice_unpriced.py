import datetime

from django.core.management.base import BaseCommand, CommandError

from allapp.billing.services import reprice_unpriced_events


class Command(BaseCommand):
    help = "Preview or reprice PENDING/UNPRICED billing events without touching locked data."

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int, required=True)
        parser.add_argument("--warehouse", type=int, required=True)
        parser.add_argument("--date-from", required=True)
        parser.add_argument("--date-to", required=True)
        parser.add_argument(
            "--apply", action="store_true", help="Persist changes; default is dry-run."
        )

    def handle(self, *args, **options):
        try:
            date_from = datetime.date.fromisoformat(options["date_from"])
            date_to = datetime.date.fromisoformat(options["date_to"])
        except ValueError as exc:
            raise CommandError("Dates must use YYYY-MM-DD.") from exc
        if date_from > date_to:
            raise CommandError("date-from cannot be later than date-to.")
        result = reprice_unpriced_events(
            owner_id=options["owner"],
            warehouse_id=options["warehouse"],
            date_from=date_from,
            date_to=date_to,
            dry_run=not options["apply"],
        )
        self.stdout.write(str(result))
