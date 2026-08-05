from django.core.management.base import BaseCommand
from django.db.models import Exists, F, OuterRef
from django.utils import timezone
from datetime import timedelta

from allapp.core.choices import ZoneType
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location
from allapp.tasking.models import ReplenishmentPolicy, WmsTask
from allapp.tasking.replenishment import source_available


class Command(BaseCommand):
    help = "Report replenishment configuration and operational readiness problems."

    def add_arguments(self, parser):
        parser.add_argument("--stale-hours", type=int, default=24)

    def handle(self, *args, **options):
        stale_hours = options["stale_hours"]
        if stale_hours <= 0:
            self.stderr.write(self.style.ERROR("--stale-hours 必须大于零。"))
            return
        stale_before = timezone.now() - timedelta(hours=stale_hours)
        invalid_targets = ReplenishmentPolicy.objects.exclude(
            target_location__zone_type=ZoneType.PICK
        ).count()
        invalid_sources = ReplenishmentPolicy.objects.filter(
            source_zone_type=ZoneType.PICK
        ).count()
        stale_snapshots = InventoryDetail.objects.exclude(
            zone_type=F("location__zone_type")
        ).count()
        frozen_policy_locations = ReplenishmentPolicy.objects.filter(
            target_location__is_frozen=True
        ).count()
        disabled_policy_locations = ReplenishmentPolicy.objects.filter(
            target_location__is_disabled=True
        ).count()
        stale_drafts = WmsTask.objects.filter(
            task_type=WmsTask.TaskType.REPLEN,
            status=WmsTask.Status.DRAFT,
            created_at__lt=stale_before,
        ).count()
        enabled_policies = list(
            ReplenishmentPolicy.objects.filter(is_active=True).select_related(
                "owner", "warehouse", "product", "target_location"
            )
        )
        policies_without_source = sum(
            1 for policy in enabled_policies if source_available(policy) <= 0
        )
        matching_policy = ReplenishmentPolicy.objects.filter(
            owner_id=OuterRef("owner_id"),
            warehouse_id=OuterRef("warehouse_id"),
            product_id=OuterRef("product_id"),
            target_location_id=OuterRef("location_id"),
            is_active=True,
        )
        pick_inventory_without_policy = (
            InventoryDetail.objects.filter(
                location__zone_type=ZoneType.PICK,
                is_active=True,
                onhand_qty__gt=0,
            )
            .annotate(has_policy=Exists(matching_policy))
            .filter(has_policy=False)
            .values("owner_id", "warehouse_id", "product_id", "location_id")
            .distinct()
            .count()
        )
        from allapp.outbound.models import OutboundOrder

        stuck_orders = OutboundOrder.objects.filter(
            approval_status="WHS_PENDING",
            is_closed=False,
            updated_at__lt=stale_before,
        ).count()
        self.stdout.write(
            "\n".join(
                [
                    f"拣选库位数量: {Location.objects.filter(zone_type=ZoneType.PICK).count()}",
                    f"存储库位数量: {Location.objects.filter(zone_type=ZoneType.STORAGE).count()}",
                    f"启用补货策略: {len(enabled_policies)}",
                    f"目标区域错误策略: {invalid_targets}",
                    f"来源区域错误策略: {invalid_sources}",
                    f"目标库位冻结策略: {frozen_policy_locations}",
                    f"目标库位停用策略: {disabled_policy_locations}",
                    f"库存区域快照不一致: {stale_snapshots}",
                    f"无可用来源库存策略: {policies_without_source}",
                    f"拣货库存缺失策略组合: {pick_inventory_without_policy}",
                    f"超过 {stale_hours} 小时未处理补货草稿: {stale_drafts}",
                    f"超过 {stale_hours} 小时等待补货订单: {stuck_orders}",
                ]
            )
        )
        if any(
            [
                invalid_targets,
                invalid_sources,
                frozen_policy_locations,
                disabled_policy_locations,
                stale_snapshots,
                policies_without_source,
                stale_drafts,
                stuck_orders,
            ]
        ):
            self.stderr.write(self.style.WARNING("补货上线准备度检查存在异常。"))
