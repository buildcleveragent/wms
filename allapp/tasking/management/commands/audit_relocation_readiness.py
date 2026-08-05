from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Container
from allapp.tasking.models import (
    ContainerUsage,
    RelocTaskExtra,
    RelocationRequest,
    RelocationReservation,
    TaskScanLog,
    WmsTask,
)


class Command(BaseCommand):
    help = "只读检查移库上线准备度、容器一致性和长期未释放的业务状态。"

    def add_arguments(self, parser):
        parser.add_argument("--stale-hours", type=int, default=24)
        parser.add_argument("--fail-on-warning", action="store_true")

    def handle(self, *args, **options):
        stale_hours = options["stale_hours"]
        if stale_hours <= 0:
            raise CommandError("--stale-hours 必须大于零。")
        stale_before = timezone.now() - timedelta(hours=stale_hours)

        inventory_location_mismatches = InventoryDetail.objects.filter(
            container__isnull=False,
        ).exclude(location_id=F("container__location_id")).count()
        inventory_warehouse_mismatches = InventoryDetail.objects.filter(
            container__isnull=False,
        ).exclude(warehouse_id=F("container__warehouse_id")).count()
        private_owner_mismatches = (
            InventoryDetail.objects.filter(
                container__isnull=False,
                container__scope=Container.Scope.PRIVATE,
            )
            .exclude(owner_id=F("container__owner_id"))
            .count()
        )
        duplicate_dimensions = self._duplicate_inventory_dimensions()
        container_cycles = self._container_cycle_count()

        stale_pending_requests = RelocationRequest.objects.filter(
            status=RelocationRequest.Status.PENDING,
            created_at__lt=stale_before,
        ).count()
        stale_reservations = RelocationReservation.objects.filter(
            status=RelocationReservation.Status.ACTIVE,
            created_at__lt=stale_before,
        ).count()
        stale_container_usages = ContainerUsage.objects.filter(
            purpose="MOVE",
            status="OPEN",
            created_at__lt=stale_before,
        ).count()
        exception_tasks = RelocTaskExtra.objects.filter(
            execution_state__in=[
                RelocTaskExtra.ExecutionState.EXCEPTION,
                RelocTaskExtra.ExecutionState.POSTING_FAILED,
            ]
        ).count()
        pending_requests = RelocationRequest.objects.filter(
            status=RelocationRequest.Status.PENDING
        ).count()
        active_reservations = RelocationReservation.objects.filter(
            status=RelocationReservation.Status.ACTIVE
        ).count()
        posting_failed_tasks = WmsTask.objects.filter(
            task_type=WmsTask.TaskType.RELOC,
            posting_status=WmsTask.PostingStatus.FAILED,
        ).count()
        voided_tasks = WmsTask.objects.filter(
            task_type=WmsTask.TaskType.RELOC,
            status=WmsTask.Status.CANCELLED,
        ).count()
        voided_scans = TaskScanLog.objects.filter(
            task__task_type=WmsTask.TaskType.RELOC,
            status=TaskScanLog.ScanStatus.IGNORED,
        ).count()

        results = [
            ("当前待审批申请", pending_requests),
            ("当前活动移库预留", active_reservations),
            ("当前过账失败任务", posting_failed_tasks),
            ("累计作废移库任务", voided_tasks),
            ("累计作废扫描", voided_scans),
            ("容器库存与容器库位不一致", inventory_location_mismatches),
            ("容器库存与容器仓库不一致", inventory_warehouse_mismatches),
            ("私有容器与库存货主不一致", private_owner_mismatches),
            ("重复库存维度", duplicate_dimensions),
            ("容器父子循环", container_cycles),
            (f"超过 {stale_hours} 小时待审批申请", stale_pending_requests),
            (f"超过 {stale_hours} 小时活动预留", stale_reservations),
            (f"超过 {stale_hours} 小时未关闭 MOVE 容器使用", stale_container_usages),
            ("异常或过账失败任务", exception_tasks),
        ]
        self.stdout.write("\n".join(f"{label}: {value}" for label, value in results))

        warning_count = sum(
            value
            for label, value in results
            if label
            not in {"当前待审批申请", "当前活动移库预留", "累计作废移库任务", "累计作废扫描"}
        )
        if warning_count:
            message = f"移库上线准备度检查发现 {warning_count} 项异常记录。"
            if options["fail_on_warning"]:
                raise CommandError(message)
            self.stderr.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS("移库上线准备度检查通过。"))

    @staticmethod
    def _duplicate_inventory_dimensions() -> int:
        """Count excess active rows sharing the complete inventory dimension."""
        dimensions = InventoryDetail.objects.filter(is_active=True).values_list(
            "owner_id",
            "warehouse_id",
            "location_id",
            "container_id",
            "product_id",
            "batch_no",
            "production_date",
            "expiry_date",
            "serial_no",
        )
        counts = Counter(
            (
                *row[:5],
                (row[5] or "").strip().upper(),
                row[6],
                row[7],
                (row[8] or "").strip().upper(),
            )
            for row in dimensions.iterator()
        )
        return sum(count - 1 for count in counts.values() if count > 1)

    @staticmethod
    def _container_cycle_count() -> int:
        """Count containers that participate in a parent cycle without recursion."""
        parent_by_id = dict(Container.objects.values_list("id", "parent_id"))
        cyclic: set[int] = set()
        resolved: set[int] = set()
        for start in parent_by_id:
            if start in resolved:
                continue
            path: list[int] = []
            position: dict[int, int] = {}
            current = start
            while current is not None and current in parent_by_id and current not in resolved:
                if current in position:
                    cyclic.update(path[position[current] :])
                    break
                position[current] = len(path)
                path.append(current)
                current = parent_by_id[current]
            resolved.update(path)
        return len(cyclic)
