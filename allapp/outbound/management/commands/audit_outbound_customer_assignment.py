from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from allapp.outbound.models import OutboundOrder


class Command(BaseCommand):
    help = (
        "只读审计可编辑标准出库单的客户业务员归属；"
        "发现非 CASH 客户与订单创建人不一致时以非零状态退出。"
    )

    def handle(self, *args, **options):
        editable_orders = (
            OutboundOrder.objects.select_related(
                "owner", "warehouse", "customer", "customer__salesperson", "created_by"
            )
            .filter(
                processing_mode=OutboundOrder.ProcessingMode.STANDARD,
                submit_status="DRAFT",
                approval_status__in=("OWNER_PENDING", "OWNER_REJECTED"),
                is_closed=False,
                customer__isnull=False,
            )
            .exclude(customer__code__iexact="CASH")
            .filter(
                Q(created_by__isnull=True)
                | ~Q(created_by_id=F("customer__salesperson_id"))
            )
            .order_by("id")
        )
        rows = [
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "owner_id": order.owner_id,
                "warehouse_id": order.warehouse_id,
                "customer_id": order.customer_id,
                "customer_code": order.customer.code,
                "customer_salesperson_id": order.customer.salesperson_id,
                "created_by_id": order.created_by_id,
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            }
            for order in editable_orders
        ]
        self.stdout.write(
            json.dumps(
                {
                    "finding_count": len(rows),
                    "orders": rows,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        if rows:
            raise CommandError(
                f"发现 {len(rows)} 张可编辑订单的客户业务员归属不一致。"
            )
