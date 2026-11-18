"""Read-only readiness probe for the Boss scheduling worker."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from allapp.reports.boss_automation import WORKER_KEY
from allapp.reports.models import BossCheckState


class Command(BaseCommand):
    help = "Require a Boss worker heartbeat within 120 seconds, optionally after container start."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--started-after", help="Timezone-aware ISO 8601 container start time")

    def handle(self, *args, **options):
        started = None
        if options["started_after"]:
            try:
                started = parse_datetime(options["started_after"])
            except ValueError:
                started = None
            if started is None or timezone.is_naive(started):
                raise CommandError("--started-after must be a timezone-aware ISO 8601 timestamp")
        try:
            state = (
                BossCheckState.objects.filter(key=WORKER_KEY).values("checked_at", "failed").first()
            )
        except DatabaseError:
            raise CommandError("Boss worker heartbeat database is unavailable") from None
        now = timezone.now()
        seen = state["checked_at"] if state else None
        # This project also supports USE_TZ=False. Docker always reports UTC;
        # interpret naive database timestamps in Django's configured local zone.
        if timezone.is_naive(now):
            now = timezone.make_aware(now, timezone.get_default_timezone())
        if seen is not None and timezone.is_naive(seen):
            seen = timezone.make_aware(seen, timezone.get_default_timezone())
        if (
            seen is None
            or state["failed"]
            or seen > now
            or now - seen > timedelta(seconds=120)
            or (started is not None and seen <= started)
        ):
            raise CommandError(
                "Boss worker heartbeat is missing, stale, or predates container start"
            )
        self.stdout.write("Boss worker heartbeat is healthy")
