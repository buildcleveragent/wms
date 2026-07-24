from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from allapp.salesapp.models import SaleMiniOrderMapping
from allapp.salesapp.services_salemini_payments import (
    safely_cancel_unpaid_mapping,
)


class Command(BaseCommand):
    help = "Cancel unpaid sale-mini orders whose payment deadline has passed."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument(
            "--close-wechat",
            action="store_true",
            help="兼容旧参数；现在始终先查询微信并安全关单。",
        )

    def handle(self, *args, **options):
        limit = max(options["limit"], 1)
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
        paid = 0
        pending = 0
        errors = 0
        for mapping_id in ids:
            try:
                mapping = SaleMiniOrderMapping.objects.get(pk=mapping_id)
                result = safely_cancel_unpaid_mapping(mapping)
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"mapping {mapping_id} failed: {type(exc).__name__}: {exc}"
                )
                continue
            state = result["result"]
            if state == "cancelled":
                expired += 1
            elif state in {"paid", "late_payment_refund_queued"}:
                paid += 1
            elif state in {"pending", "unknown"}:
                pending += 1
                if result.get("error"):
                    errors += 1
                    self.stderr.write(
                        f"payment status unresolved for mapping {mapping_id}: "
                        f"{result['error']}"
                    )

        self.stdout.write(
            f"expired={expired} paid={paid} pending={pending} errors={errors}"
        )
        if errors:
            raise CommandError(f"{errors} 个超时订单处理失败，其余记录已继续处理。")
