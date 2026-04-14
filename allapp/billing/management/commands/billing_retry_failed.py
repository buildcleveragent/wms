# allapp/billing/management/commands/billing_retry_failed.py
"""
扫描过账成功但 billing 失败的 PostingJournal，重新触发费用应计。

用法:
    python manage.py billing_retry_failed
    python manage.py billing_retry_failed --dry-run
    python manage.py billing_retry_failed --limit 50
"""
import logging

from django.core.management.base import BaseCommand

from allapp.billing import services as billing_services
from allapp.billing.services.accrual import AUTO_REVIEW_ORDER_PROCESSING_METHODS
from allapp.inventory.models import PostingJournal
from allapp.tasking.models import WmsTask

logger = logging.getLogger("allapp.billing")

BILLING_FAILED_MARKER = "BILLING_FAILED"


class Command(BaseCommand):
    help = "Retry billing accrual for PostingJournals that failed during posting."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only list failed PJs, don't retry")
        parser.add_argument("--limit", type=int, default=200, help="Max PJs to process (default 200)")

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        limit = opts["limit"]

        failed_pjs = (
            PostingJournal.objects
            .filter(status="POSTED", message__contains=BILLING_FAILED_MARKER)
            .order_by("id")[:limit]
        )

        found = 0
        retried = 0
        errors = 0

        for pj in failed_pjs:
            found += 1
            if pj.src_model != "WmsTask":
                self.stdout.write(f"  SKIP PJ#{pj.id}: src_model={pj.src_model} (only WmsTask supported)")
                continue

            task = WmsTask.objects.filter(pk=pj.src_id).first()
            if not task:
                self.stdout.write(f"  SKIP PJ#{pj.id}: WmsTask#{pj.src_id} not found")
                continue

            if dry_run:
                self.stdout.write(f"  DRY-RUN PJ#{pj.id}: task={task.task_no} type={task.task_type}")
                continue

            try:
                billing_services.accrue_for_posting(task, pj, by_user=None)

                if task.task_type == WmsTask.TaskType.REVIEW:
                    billing_services.accrue_order_processing_for_task(
                        task, pj, by_user=None,
                        allowed_methods=AUTO_REVIEW_ORDER_PROCESSING_METHODS,
                    )

                # 清除失败标记（确保 BILLING_RETRIED 不被截断）
                new_msg = pj.message.replace(BILLING_FAILED_MARKER, "BILLING_RETRIED")
                if len(new_msg) > 255:
                    # 保留末尾的 marker 完整，截断前面的内容
                    new_msg = new_msg[:252] + "..."
                pj.message = new_msg
                pj.save(update_fields=["message", "updated_at"])
                retried += 1
                self.stdout.write(f"  OK PJ#{pj.id}: task={task.task_no}")
            except Exception as e:
                errors += 1
                logger.exception("billing_retry_failed: PJ#%s task=%s err=%s", pj.id, task.task_no, e)
                self.stderr.write(f"  FAIL PJ#{pj.id}: {e}")

        self.stdout.write(
            f"\nDone. found={found} retried={retried} errors={errors}"
            + (" (dry-run)" if dry_run else "")
        )
