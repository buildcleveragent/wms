import datetime
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from allapp.billing.enums import MetricType
from allapp.billing.services import run_scheduled_metric_generation_for_dates


class Command(BaseCommand):
    help = "Run the daily BillingMetricDaily scheduler."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run due dates once and exit.")
        parser.add_argument(
            "--date",
            action="append",
            dest="dates",
            help="YYYY-MM-DD service date. Repeatable. Requires --once.",
        )
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
        parser.add_argument("--force", action="store_true", help="Re-run even if the date already succeeded.")

    def _parse_explicit_dates(self, date_values):
        dates = []
        for value in date_values or []:
            try:
                dates.append(datetime.date.fromisoformat(value))
            except ValueError as exc:
                raise CommandError(f"Invalid --date value: {value}") from exc
        return sorted(set(dates))

    def _due_service_dates(self, now: datetime.datetime):
        scheduled_time = datetime.time(
            hour=max(0, min(23, int(settings.BILLING_METRIC_SCHEDULER_HOUR))),
            minute=max(0, min(59, int(settings.BILLING_METRIC_SCHEDULER_MINUTE))),
        )
        if now.time() < scheduled_time:
            return []

        lookback_days = max(1, int(settings.BILLING_METRIC_SCHEDULER_LOOKBACK_DAYS))
        return [now.date() - datetime.timedelta(days=offset) for offset in range(1, lookback_days + 1)]

    def _resolve_service_dates(self, opts):
        explicit_dates = self._parse_explicit_dates(opts.get("dates"))
        if explicit_dates:
            return explicit_dates
        return sorted(self._due_service_dates(timezone.now()))

    def _write_summary(self, summary):
        dates = ",".join(service_date.isoformat() for service_date in summary["service_dates"]) or "-"
        self.stdout.write(
            "Billing metric scheduler: "
            f"dates={dates}, scopes_total={summary['scopes_total']}, "
            f"success={summary['success']}, failed={summary['failed']}, "
            f"skipped_success={summary['skipped_success']}, skipped_running={summary['skipped_running']}, "
            f"created={summary['created']}, updated={summary['updated']}, "
            f"deleted_zero={summary['deleted_zero']}, skipped_zero={summary['skipped_zero']}, "
            f"skipped_manual={summary['skipped_manual']}, unsupported={summary['unsupported']}, "
            f"noop={summary['noop']}"
        )
        for run in summary["runs"]:
            if run["status"] == "failed":
                self.stderr.write(
                    f"Billing metric scheduler failed for owner={run['owner_id']} "
                    f"warehouse={run['warehouse_id']} service_date={run['service_date']}: {run['message']}"
                )

    def _run_once(self, opts, *, print_when_fully_skipped: bool):
        service_dates = self._resolve_service_dates(opts)
        if not service_dates:
            return None

        summary = run_scheduled_metric_generation_for_dates(
            service_dates,
            owner_id=opts.get("owner"),
            warehouse_id=opts.get("warehouse"),
            metric_types=opts.get("metric_types"),
            overwrite=opts.get("overwrite", False),
            allow_area_fallback=(
                opts.get("allow_area_fallback", False)
                or settings.BILLING_METRIC_SCHEDULER_ALLOW_AREA_FALLBACK
            ),
            force=opts.get("force", False),
        )
        if print_when_fully_skipped or summary["success"] or summary["failed"] or summary["skipped_running"]:
            self._write_summary(summary)
        if summary["failed"]:
            raise CommandError(f"Billing metric scheduler finished with {summary['failed']} failed scope(s).")
        return summary

    def handle(self, *args, **opts):
        if opts.get("dates") and not opts.get("once"):
            raise CommandError("--date requires --once.")

        if opts.get("once"):
            summary = self._run_once(opts, print_when_fully_skipped=True)
            if summary is None:
                self.stdout.write("Billing metric scheduler: no service dates are due.")
            return

        if not settings.BILLING_METRIC_SCHEDULER_ENABLED:
            self.stdout.write("Billing metric scheduler disabled by settings.")
            return

        poll_seconds = max(5, int(settings.BILLING_METRIC_SCHEDULER_POLL_SECONDS))
        self.stdout.write(
            "Billing metric scheduler started: "
            f"time={settings.BILLING_METRIC_SCHEDULER_HOUR:02d}:{settings.BILLING_METRIC_SCHEDULER_MINUTE:02d}, "
            f"lookback_days={settings.BILLING_METRIC_SCHEDULER_LOOKBACK_DAYS}, "
            f"poll_seconds={poll_seconds}"
        )

        try:
            while True:
                try:
                    self._run_once(opts, print_when_fully_skipped=False)
                except CommandError as exc:
                    self.stderr.write(str(exc))
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            self.stdout.write("Billing metric scheduler stopped.")
