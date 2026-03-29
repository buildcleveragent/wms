import datetime

from django.core.management.base import BaseCommand, CommandError

from allapp.inventory.snapshot_services import generate_inventory_snapshots_for_dates


class Command(BaseCommand):
    help = "Generate InventorySnapshotDaily rows for one or more service dates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            action="append",
            dest="dates",
            help="YYYY-MM-DD snapshot date. Repeatable.",
        )
        parser.add_argument("--date-from", dest="date_from", help="Start date in YYYY-MM-DD.")
        parser.add_argument("--date-to", dest="date_to", help="End date in YYYY-MM-DD.")
        parser.add_argument("--owner", type=int, help="owner_id (optional)")
        parser.add_argument("--warehouse", type=int, help="warehouse_id (optional)")
        parser.add_argument(
            "--bootstrap",
            action="store_true",
            help="Bootstrap the first generated day from current InventoryDetail.",
        )

    def _parse_date(self, raw_value, *, option_name):
        try:
            return datetime.date.fromisoformat(raw_value)
        except ValueError as exc:
            raise CommandError(f"Invalid {option_name} value: {raw_value}") from exc

    def _resolve_dates(self, opts):
        explicit_dates = [
            self._parse_date(value, option_name="--date")
            for value in (opts.get("dates") or [])
        ]
        if explicit_dates and (opts.get("date_from") or opts.get("date_to")):
            raise CommandError("Use either --date or --date-from/--date-to, not both.")

        if explicit_dates:
            return sorted(set(explicit_dates))

        date_from_raw = opts.get("date_from")
        date_to_raw = opts.get("date_to")
        if not date_from_raw or not date_to_raw:
            raise CommandError("Provide --date or both --date-from and --date-to.")

        date_from = self._parse_date(date_from_raw, option_name="--date-from")
        date_to = self._parse_date(date_to_raw, option_name="--date-to")
        if date_from > date_to:
            raise CommandError("--date-from must be earlier than or equal to --date-to.")

        days = []
        current = date_from
        while current <= date_to:
            days.append(current)
            current += datetime.timedelta(days=1)
        return days

    def handle(self, *args, **opts):
        service_dates = self._resolve_dates(opts)
        summary = generate_inventory_snapshots_for_dates(
            service_dates,
            owner_id=opts.get("owner"),
            warehouse_id=opts.get("warehouse"),
            bootstrap_first=opts.get("bootstrap", False),
        )

        self.stdout.write(
            "Inventory snapshot generation: "
            f"dates={','.join(day.isoformat() for day in summary['service_dates'])}, "
            f"rows_created={summary['rows_created']}, "
            f"scopes_processed={summary['scopes_processed']}"
        )
        for day_summary in summary["days"]:
            self.stdout.write(
                f"  {day_summary['service_date'].isoformat()} "
                f"mode={day_summary['mode']} "
                f"rows_created={day_summary['rows_created']} "
                f"scopes_processed={day_summary['scopes_processed']}"
            )
