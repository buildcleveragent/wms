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
                actor = task.posted_by
                billing_services.accrue_for_posting(task, pj, by_user=actor)

                should_accrue_order_processing = (
                    task.task_type == WmsTask.TaskType.REVIEW
                    or (
                        task.task_type == WmsTask.TaskType.PICK
                        and task.review_status == WmsTask.ReviewStatus.APPROVED
                    )
                )
                if should_accrue_order_processing:
                    billing_services.accrue_order_processing_for_task(
                        task,
                        pj,
                        by_user=actor,
                        allowed_methods=AUTO_REVIEW_ORDER_PROCESSING_METHODS,
                    )

                # 移除失败详情，并在长消息下也始终保留完整的成功尾标。
                failed_at = pj.message.find(BILLING_FAILED_MARKER)
                base_message = pj.message[:failed_at].rstrip("|: ")
                retried_marker = "|BILLING_RETRIED"
                base_message = base_message[: 255 - len(retried_marker)]
                pj.message = f"{base_message}{retried_marker}"
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
