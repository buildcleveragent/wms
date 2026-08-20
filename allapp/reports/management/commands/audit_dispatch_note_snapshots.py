"""Read-only audit for historical dispatch-note snapshots."""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from allapp.reports.dispatch_note_builder import (
    DispatchNoteDataError,
    build_dispatch_note,
)
from allapp.reports.models import ReportSnapshot
from allapp.tasking.models import WmsTask


class Command(BaseCommand):
    help = "只读审计历史配送单的零金额、错误来源和金额不一致问题"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--fail-on-issues", action="store_true")

    def handle(self, *args, **options):
        issues = []
        snapshots = ReportSnapshot.objects.filter(doc_type="DISPATCH_NOTE").order_by("id")
        for snapshot in snapshots[: options["limit"]]:
            reasons = []
            if snapshot.tpl_ver == "v1" and snapshot.amount_total == Decimal("0.00"):
                reasons.append("historical_v1_zero_amount_requires_review")
            try:
                task = WmsTask.objects.get(pk=snapshot.src_id)
                note = build_dispatch_note(task.pk)
                if note.is_preview and snapshot.is_final:
                    reasons.append("preview_marked_final")
                try:
                    stored_amount = Decimal(str(snapshot.amount_total))
                except InvalidOperation:
                    reasons.append("invalid_stored_amount")
                else:
                    if stored_amount != note.total_amount:
                        reasons.append("amount_mismatch")
            except WmsTask.DoesNotExist:
                reasons.append("source_task_missing")
            except DispatchNoteDataError:
                reasons.append("source_mapping_invalid")

            if reasons:
                issues.append((snapshot.id, snapshot.doc_no, reasons))
                self.stdout.write(
                    f"snapshot={snapshot.id} doc_no={snapshot.doc_no!r} issues={','.join(reasons)}"
                )

        self.stdout.write(
            f"audited={min(snapshots.count(), options['limit'])} issues={len(issues)}"
        )
        if issues and options["fail_on_issues"]:
            raise CommandError(f"发现 {len(issues)} 份待业务复核的配送单快照")
