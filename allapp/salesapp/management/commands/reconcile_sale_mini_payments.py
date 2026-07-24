from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from allapp.salesapp.models import (
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniRefund,
)
from allapp.salesapp.services_salemini_payments import (
    query_and_apply_payment,
    reconcile_refund,
    safely_cancel_unpaid_mapping,
)


class Command(BaseCommand):
    help = "Reconcile overdue WeChat payments and pending/retryable refunds."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        limit = max(options["limit"], 1)
        now = timezone.now()
        payment_ids = list(
            SaleMiniOrderMapping.objects.filter(
                payment_status=SaleMiniOrderMapping.PaymentStatus.UNPAID,
                pay_deadline_at__isnull=False,
                pay_deadline_at__lte=now,
            )
            .order_by("pay_deadline_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        payment_counts = {"cancelled": 0, "paid": 0, "pending": 0, "failed": 0}
        for mapping_id in payment_ids:
            try:
                result = safely_cancel_unpaid_mapping(
                    SaleMiniOrderMapping.objects.get(pk=mapping_id)
                )
            except Exception as exc:
                payment_counts["failed"] += 1
                self.stderr.write(
                    f"mapping {mapping_id} failed: {type(exc).__name__}: {exc}"
                )
                continue
            state = result["result"]
            if state == "cancelled":
                payment_counts["cancelled"] += 1
            elif state in {"paid", "late_payment_refund_queued"}:
                payment_counts["paid"] += 1
            else:
                payment_counts["pending"] += 1

        intent_ids = list(
            SaleMiniPayment.objects.filter(
                channel=SaleMiniPayment.Channel.WECHAT_JSAPI,
                status__in=[
                    SaleMiniPayment.Status.CREATED,
                    SaleMiniPayment.Status.PREPAY,
                ],
                requires_manual_action=False,
                next_reconcile_at__isnull=False,
                next_reconcile_at__lte=now,
            )
            .order_by("next_reconcile_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        intent_counts = {"paid": 0, "waiting": 0, "failed": 0}
        for payment_id in intent_ids:
            try:
                result = query_and_apply_payment(
                    SaleMiniPayment.objects.get(pk=payment_id)
                )
            except Exception as exc:
                intent_counts["failed"] += 1
                self.stderr.write(
                    f"payment {payment_id} failed: {type(exc).__name__}: {exc}"
                )
                continue
            if result["trade_state"] == "SUCCESS":
                intent_counts["paid"] += 1
            else:
                intent_counts["waiting"] += 1

        refund_ids = list(
            SaleMiniRefund.objects.filter(
                requires_manual_action=False,
                status__in=[
                    SaleMiniRefund.Status.CREATED,
                    SaleMiniRefund.Status.PROCESSING,
                    SaleMiniRefund.Status.FAILED,
                ],
                next_retry_at__isnull=False,
                next_retry_at__lte=now,
            )
            .order_by("next_retry_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        refund_counts = {"success": 0, "processing": 0, "manual": 0, "failed": 0}
        for refund_id in refund_ids:
            try:
                refund = SaleMiniRefund.objects.get(pk=refund_id)
                if refund.next_retry_at and refund.next_retry_at > timezone.now():
                    continue
                refund = reconcile_refund(refund)
            except Exception as exc:
                refund_counts["failed"] += 1
                self.stderr.write(
                    f"refund {refund_id} failed: {type(exc).__name__}: {exc}"
                )
                continue
            if refund.status == SaleMiniRefund.Status.SUCCESS:
                refund_counts["success"] += 1
            elif refund.requires_manual_action:
                refund_counts["manual"] += 1
            else:
                refund_counts["processing"] += 1

        self.stdout.write(
            "orders="
            f"{payment_counts} payments={intent_counts} refunds={refund_counts}"
        )
        failures = (
            payment_counts["failed"]
            + intent_counts["failed"]
            + refund_counts["failed"]
        )
        if failures:
            raise CommandError(f"{failures} 条支付或退款记录处理失败，其余记录已继续处理。")
