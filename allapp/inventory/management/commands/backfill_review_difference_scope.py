import re

from django.core.management.base import BaseCommand
from django.db import transaction

from allapp.inventory.models import ReviewDifference
from allapp.tasking.models import WmsTask, WmsTaskLine


SOURCE_RE = re.compile(r"来源任务:([^,，\s]+)(?:[,，]\s*行:(\d+))?")


class Command(BaseCommand):
    help = "确定性回填盘点差异货主与来源任务；默认只预览，绝不猜测多货主记录。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="实际写入；默认 dry-run"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = options["apply"]
        scanned = resolved = unresolved = 0
        for difference in ReviewDifference.objects.filter(
            owner__isnull=True
        ).iterator():
            scanned += 1
            owner_id = task_id = line_id = None
            match = SOURCE_RE.search(difference.note or "")
            if match:
                task = WmsTask.objects.filter(
                    task_no=match.group(1),
                    warehouse_id=difference.warehouse_id,
                ).first()
                if task:
                    owner_id, task_id = task.owner_id, task.id
                    if match.group(2):
                        line_id = (
                            WmsTaskLine.objects.filter(
                                pk=int(match.group(2)), task_id=task.id
                            )
                            .values_list("id", flat=True)
                            .first()
                        )
            if owner_id is None:
                owners = list(
                    difference.lines.values_list("product__owner_id", flat=True)
                    .distinct()
                    .order_by("product__owner_id")[:2]
                )
                if len(owners) == 1:
                    owner_id = owners[0]
            if owner_id is None:
                unresolved += 1
                self.stdout.write(f"UNRESOLVED difference={difference.id}")
                continue
            resolved += 1
            self.stdout.write(
                f"RESOLVED difference={difference.id} owner={owner_id} task={task_id or '-'}"
            )
            if apply_changes:
                ReviewDifference.objects.filter(pk=difference.pk).update(
                    owner_id=owner_id,
                    source_task_id=task_id,
                    source_task_line_id=line_id,
                )
        if not apply_changes:
            transaction.set_rollback(True)
        self.stdout.write(
            self.style.SUCCESS(
                f"mode={'APPLY' if apply_changes else 'DRY_RUN'} scanned={scanned} "
                f"resolved={resolved} unresolved={unresolved}"
            )
        )
