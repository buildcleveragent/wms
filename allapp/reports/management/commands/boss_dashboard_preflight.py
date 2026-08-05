import json

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.billing.enums import BillStatus
from allapp.billing.models import Bill, BillingAccrual
from allapp.inventory.models import InventorySnapshotDaily, ReviewDifference


class Command(BaseCommand):
    help = "只读检查老板看板多币种、到期日、盘点归属、快照和多仓授权质量。"

    def handle(self, *args, **options):
        currencies = sorted(
            {
                str(value or "UNKNOWN").strip().upper() or "UNKNOWN"
                for value in list(
                    Bill.objects.values_list("currency", flat=True).distinct()
                )
                + list(
                    BillingAccrual.objects.values_list("currency", flat=True).distinct()
                )
            }
        )
        missing_due = Bill.objects.filter(
            status__in=[BillStatus.ISSUED, BillStatus.PAID],
            due_date__isnull=True,
        )
        ambiguous_differences = ReviewDifference.objects.filter(owner__isnull=True)
        snapshot_rows = InventorySnapshotDaily.objects.aggregate(
            rows=Count("id"),
            days=Count("snapshot_date", distinct=True),
            missing_units=Count("id", filter=Q(base_unit_code="")),
            inferred_units=Count(
                "id",
                filter=Q(
                    base_unit_source=InventorySnapshotDaily.UnitSource.LEGACY_INFERRED
                ),
            ),
            approximate=Count(
                "id",
                filter=Q(
                    snapshot_source__in=[
                        InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
                        InventorySnapshotDaily.Source.TX_ROLLFORWARD_APPROX,
                    ]
                ),
            ),
        )
        multi_warehouse_bosses = list(
            UserRoleScope.objects.filter(
                role=UserRoleScope.Role.WAREHOUSE_BOSS,
                is_active=True,
            )
            .values("user_id", "user__username")
            .annotate(warehouse_count=Count("warehouse_id", distinct=True))
            .filter(warehouse_count__gt=1)
            .order_by("user_id")
        )
        authorized_warehouses = {
            row.warehouse_id
            for row in UserRoleScope.objects.filter(
                role=UserRoleScope.Role.WAREHOUSE_BOSS,
                is_active=True,
            )
        }
        warehouses_without_owner_binding = sorted(
            warehouse_id
            for warehouse_id in authorized_warehouses
            if not OwnerWarehouseBinding.objects.filter(
                warehouse_id=warehouse_id,
                is_active=True,
                is_deleted=False,
            ).exists()
        )
        result = {
            "currencies": currencies,
            "unknown_currency_bills": Bill.objects.filter(
                Q(currency__isnull=True) | Q(currency="")
            ).count(),
            "unknown_currency_accruals": BillingAccrual.objects.filter(
                Q(currency__isnull=True) | Q(currency="")
            ).count(),
            "missing_due_date_count": missing_due.count(),
            "missing_due_date_ids": list(
                missing_due.order_by("id").values_list("id", flat=True)[:200]
            ),
            "review_difference_owner_unknown_count": ambiguous_differences.count(),
            "review_difference_owner_unknown_ids": list(
                ambiguous_differences.order_by("id").values_list("id", flat=True)[:200]
            ),
            "inventory_snapshots": snapshot_rows,
            "multi_warehouse_bosses": multi_warehouse_bosses,
            "warehouses_without_owner_binding": warehouses_without_owner_binding,
        }
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str, indent=2))
