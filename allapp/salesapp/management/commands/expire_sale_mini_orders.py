from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from allapp.outbound.services import unallocate_for_order
from allapp.salesapp.models import SaleMiniOrderMapping, SaleMiniPayment
from allapp.salesapp.services_salemini_adjustments import (
    release_adjustments,
    reverse_distribution,
)
from allapp.salesapp.services_wechat_pay import (
    WechatPayConfigError,
    WechatPayRequestError,
    close_jsapi_payment,
)


class Command(BaseCommand):
    help = "Cancel unpaid sale-mini orders whose payment deadline has passed."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument(
            "--close-wechat",
            action="store_true",
            help="Try to close active WeChat prepay orders before local cancellation.",
        )

    def handle(self, *args, **options):
        limit = max(options["limit"], 1)
        close_wechat = options["close_wechat"]
        now = timezone.now()
        ids = list(
            SaleMiniOrderMapping.objects.filter(
                payment_status=SaleMiniOrderMapping.PaymentStatus.UNPAID,
                pay_deadline_at__isnull=False,
                pay_deadline_at__lte=now,
            )
            .order_by("pay_deadline_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        expired = 0
        close_failed = 0
        for mapping_id in ids:
            with transaction.atomic():
                mapping = (
                    SaleMiniOrderMapping.objects.select_for_update()
                    .select_related("outbound_order")
                    .get(pk=mapping_id)
                )
                if mapping.payment_status != SaleMiniOrderMapping.PaymentStatus.UNPAID:
                    continue
                if mapping.pay_deadline_at and mapping.pay_deadline_at > now:
                    continue
                payment = (
                    mapping.payments.select_for_update()
                    .filter(
                        channel=SaleMiniPayment.Channel.WECHAT_JSAPI,
                        status__in=[
                            SaleMiniPayment.Status.CREATED,
                            SaleMiniPayment.Status.PREPAY,
                        ],
                    )
                    .order_by("-created_at", "-id")
                    .first()
                )
                if close_wechat and payment:
                    try:
                        close_jsapi_payment(payment)
                        payment.status = SaleMiniPayment.Status.CLOSED
                        payment.closed_at = timezone.now()
                        payment.save(
                            update_fields=["status", "closed_at", "updated_at"]
                        )
                    except (WechatPayConfigError, WechatPayRequestError) as exc:
                        close_failed += 1
                        self.stderr.write(
                            f"close wechat payment failed for {payment.out_trade_no}: {exc}"
                        )

                order = mapping.outbound_order
                if order.approval_status != "CANCELLED":
                    unallocate_for_order(order)
                    order.approval_status = "CANCELLED"
                    order.save(update_fields=["approval_status", "updated_at"])
                release_adjustments(mapping)
                reverse_distribution(mapping)
                mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.CANCELLED
                mapping.save(update_fields=["payment_status", "updated_at"])
                expired += 1

        self.stdout.write(
            self.style.SUCCESS(f"expired={expired} close_wechat_failed={close_failed}")
        )
