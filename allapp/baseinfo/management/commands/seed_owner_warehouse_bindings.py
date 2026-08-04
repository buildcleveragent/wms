"""Discover and optionally create explicit owner-to-warehouse bindings."""

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.billing.models import BillingRule
from allapp.inbound.models import InboundOrder
from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder
from allapp.tasking.models import WmsTask


def collect_binding_candidates():
    """Return ``(owner_id, warehouse_id) -> source names`` from existing data."""

    candidates = defaultdict(set)
    sources = (
        (
            "inventory",
            InventoryDetail.objects.filter(
                is_active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
        (
            "inbound_order",
            InboundOrder.objects.filter(
                is_active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
        (
            "outbound_order",
            OutboundOrder.objects.filter(
                is_active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
        (
            "wms_task",
            WmsTask.objects.filter(
                is_active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
        (
            "billing_rule",
            BillingRule.objects.filter(
                active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
        (
            "legacy_user",
            get_user_model().objects.filter(
                is_active=True,
                owner_id__isnull=False,
                warehouse_id__isnull=False,
            ),
        ),
    )

    for source_name, queryset in sources:
        for owner_id, warehouse_id in queryset.values_list(
            "owner_id", "warehouse_id"
        ).distinct():
            candidates[(int(owner_id), int(warehouse_id))].add(source_name)
    return candidates


class Command(BaseCommand):
    help = (
        "从现有业务数据预览或初始化货主—仓库关联；默认只预览，"
        "使用 --apply 才会写入。"
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="只输出候选关联（默认）。",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="创建候选关联，并重新启用已存在但停用或软删除的关联。",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        candidates = collect_binding_candidates()
        apply_changes = bool(options["apply"])

        created = reactivated = unchanged = 0
        for (owner_id, warehouse_id), source_names in sorted(candidates.items()):
            sources_text = ",".join(sorted(source_names))
            state = "candidate"
            if apply_changes:
                binding = OwnerWarehouseBinding.all_objects.filter(
                    owner_id=owner_id,
                    warehouse_id=warehouse_id,
                ).first()
                if binding is None:
                    OwnerWarehouseBinding.all_objects.create(
                        owner_id=owner_id,
                        warehouse_id=warehouse_id,
                        is_active=True,
                        remark=f"初始化来源：{sources_text}",
                    )
                    created += 1
                    state = "created"
                elif binding.is_deleted or not binding.is_active:
                    binding.is_deleted = False
                    binding.deleted_at = None
                    binding.deleted_by = None
                    binding.is_active = True
                    binding.save(
                        update_fields=(
                            "is_deleted",
                            "deleted_at",
                            "deleted_by",
                            "is_active",
                            "updated_at",
                        )
                    )
                    reactivated += 1
                    state = "reactivated"
                else:
                    unchanged += 1
                    state = "unchanged"

            self.stdout.write(
                f"owner_id={owner_id} warehouse_id={warehouse_id} "
                f"sources={sources_text} state={state}"
            )

        mode_text = "apply" if apply_changes else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                f"mode={mode_text} candidates={len(candidates)} created={created} "
                f"reactivated={reactivated} unchanged={unchanged}"
            )
        )
