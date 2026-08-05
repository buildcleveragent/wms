from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from allapp.reports.models import TaskStateSnapshotDaily
from allapp.tasking.models import WmsTask


class Command(BaseCommand):
    help = "Capture end-of-day open task state for historical backlog reporting."

    def add_arguments(self, parser):
        parser.add_argument("--date", dest="snapshot_date")

    @transaction.atomic
    def handle(self, *args, **options):
        current = timezone.now()
        today = (
            timezone.localtime(current).date()
            if timezone.is_aware(current)
            else current.date()
        )
        snapshot_date = (
            timezone.datetime.fromisoformat(options["snapshot_date"]).date()
            if options.get("snapshot_date")
            else today
        )
        now = timezone.now()
        rows = []
        qs = WmsTask.objects.exclude(
            status__in=[WmsTask.Status.COMPLETED, WmsTask.Status.CANCELLED]
        ).filter(created_at__date__lte=snapshot_date)
        for task in qs.iterator():
            anchor = task.started_at or task.released_at or task.created_at
            age = max(0, int((now - anchor).total_seconds() // 60)) if anchor else 0
            rows.append(
                TaskStateSnapshotDaily(
                    snapshot_date=snapshot_date,
                    owner_id=task.owner_id,
                    warehouse_id=task.warehouse_id,
                    task_id=task.pk,
                    status=task.status,
                    age_minutes=age,
                )
            )
        TaskStateSnapshotDaily.objects.bulk_create(
            rows,
            update_conflicts=True,
            update_fields=["owner", "warehouse", "status", "age_minutes"],
            unique_fields=["snapshot_date", "task"],
        )
        self.stdout.write(
            self.style.SUCCESS(f"captured={len(rows)} date={snapshot_date}")
        )
