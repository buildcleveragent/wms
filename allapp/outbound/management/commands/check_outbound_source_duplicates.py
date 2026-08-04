from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.db.models.functions import Lower, Trim

from allapp.outbound.models import OutboundOrder


class Command(BaseCommand):
    help = "只读检查建立货主+源单号唯一约束前的历史重复订单。"

    def handle(self, *args, **options):
        groups = list(
            OutboundOrder.all_objects.exclude(src_bill_no__isnull=True)
            .annotate(normalized_src=Lower(Trim("src_bill_no")))
            .exclude(normalized_src="")
            .values("owner_id", "normalized_src")
            .annotate(order_count=Count("id"))
            .filter(order_count__gt=1)
            .order_by("owner_id", "normalized_src")
        )
        if not groups:
            self.stdout.write(self.style.SUCCESS("未发现重复的货主源单号。"))
            return

        details = []
        for group in groups:
            owner_id = group["owner_id"]
            normalized = group["normalized_src"]
            order_ids = list(
                OutboundOrder.all_objects.filter(owner_id=owner_id)
                .annotate(normalized_src=Lower(Trim("src_bill_no")))
                .filter(normalized_src=normalized)
                .order_by("id")
                .values_list("id", flat=True)
            )
            details.append(
                f"owner_id={owner_id}, src_bill_no={normalized!r}, order_ids={order_ids}"
            )

        raise CommandError(
            "发现重复的货主源单号，禁止应用唯一约束：" + "; ".join(details)
        )
