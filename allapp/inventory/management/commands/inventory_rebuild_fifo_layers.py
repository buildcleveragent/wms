from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.inventory.fifo import create_receipt_layer
from allapp.inventory.models import (
    InventoryCostLayer,
    InventoryDetail,
    InventoryLayerPosition,
)


class Command(BaseCommand):
    help = (
        "Preview or create conservative LEGACY_OPENING FIFO layers for current stock."
    )

    def add_arguments(self, parser):
        parser.add_argument("--owner", type=int)
        parser.add_argument("--warehouse", type=int)
        parser.add_argument("--commit", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        details = InventoryDetail.objects.filter(onhand_qty__gt=0)
        if options.get("owner"):
            details = details.filter(owner_id=options["owner"])
        if options.get("warehouse"):
            details = details.filter(warehouse_id=options["warehouse"])
        existing = InventoryCostLayer.objects.all()
        if options.get("owner"):
            existing = existing.filter(owner_id=options["owner"])
        if options.get("warehouse"):
            existing = existing.filter(warehouse_id=options["warehouse"])
        if options["commit"] and existing.exists():
            raise CommandError(
                "FIFO layers already exist in the requested scope; refusing to overlay them."
            )
        total = details.count()
        quantity_by_uom = {}
        for detail in details.select_related("product__base_uom").iterator():
            code = detail.product.base_uom.code
            quantity_by_uom[code] = (
                quantity_by_uom.get(code, Decimal("0")) + detail.onhand_qty
            )
        if not options["commit"]:
            self.stdout.write(
                f"dry_run=True rows={total} quantity_by_uom={quantity_by_uom} "
                "cost_quality=LEGACY_OPENING"
            )
            return
        created = 0
        for detail in details.select_related("product").iterator():
            layer, was_created = create_receipt_layer(
                owner_id=detail.owner_id,
                warehouse_id=detail.warehouse_id,
                product_id=detail.product_id,
                location_id=detail.location_id,
                quantity=detail.onhand_qty,
                source_type="LEGACY_OPENING",
                source_id=detail.pk,
                batch_no=detail.batch_no,
                serial_no=detail.serial_no,
                expiry_date=detail.expiry_date,
                container_id=detail.container_id,
            )
            if was_created:
                InventoryCostLayer.objects.filter(pk=layer.pk).update(
                    cost_quality=InventoryCostLayer.CostQuality.LEGACY_OPENING
                )
                created += 1
        layer_qty = InventoryLayerPosition.objects.filter(
            layer__source_type="LEGACY_OPENING",
            layer__owner_id__in=details.values("owner_id"),
            layer__warehouse_id__in=details.values("warehouse_id"),
        )
        rebuilt_by_uom = {}
        for row in layer_qty.select_related("layer__base_uom").iterator():
            code = row.layer.base_uom.code
            rebuilt_by_uom[code] = (
                rebuilt_by_uom.get(code, Decimal("0")) + row.remaining_qty
            )
        if rebuilt_by_uom != quantity_by_uom:
            raise CommandError(
                "FIFO reconciliation failed: "
                f"inventory={quantity_by_uom}, layers={rebuilt_by_uom}"
            )
        self.stdout.write(
            self.style.SUCCESS(f"created={created} quantity_by_uom={rebuilt_by_uom}")
        )
