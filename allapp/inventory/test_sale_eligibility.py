from datetime import date, datetime, timedelta, timezone

from django.test import TestCase

from allapp.inventory.models import InventoryDetail
from allapp.inventory.sale_eligibility import sale_layers
from allapp.locations.models import Location
from allapp.outbound.test_reorder import ReorderFixture


class SaleEligibilityTests(ReorderFixture, TestCase):
    def test_shanghai_midnight_inclusive_expiry_and_controlled_source_requirements(self):
        monday = date(2026, 11, 16)
        self.stock.expiry_date = monday
        self.stock.production_date = monday - timedelta(days=3)
        self.stock.save()
        self.product.expiry_control = True
        self.product.expiry_basis = "MFG"
        self.product.shelf_life_days = 3
        self.product.expiry_warning_days = 1
        self.product.save()
        scope = InventoryDetail.objects.filter(pk=self.stock.pk)
        before_midnight = datetime(2026, 11, 16, 15, 59, tzinfo=timezone.utc)
        at_midnight = datetime(2026, 11, 16, 16, 0, tzinfo=timezone.utc)
        self.assertTrue(sale_layers(scope, now=before_midnight).exists())
        self.assertFalse(sale_layers(scope, now=at_midnight).exists())
        scope.update(production_date=None)
        self.assertFalse(sale_layers(scope, now=before_midnight).exists())
        self.product.expiry_basis = "INBOUND"
        self.product.inbound_valid_days = 3
        self.product.shelf_life_days = None
        self.product.save()
        self.assertTrue(sale_layers(scope, now=before_midnight).exists())
        scope.update(expiry_date=None)
        self.assertFalse(sale_layers(scope, now=before_midnight).exists())
        self.product.expiry_control = False
        self.product.save()
        self.assertTrue(sale_layers(scope, now=at_midnight).exists())
        for field, value in [
            ("is_frozen", True),
            ("is_disabled", True),
            ("is_active", False),
            ("is_deleted", True),
        ]:
            with self.subTest(field=field):
                Location.all_objects.filter(pk=self.loc.pk).update(**{field: value})
                self.assertFalse(sale_layers(scope).exists())
                Location.all_objects.filter(pk=self.loc.pk).update(**{field: not value})

    def test_owner_approve_reserves_only_valid_fefo_stock(self):
        from django.utils import timezone as django_timezone
        from allapp.tasking.models import WmsTaskLine

        today = django_timezone.now().date()
        self.product.expiry_control = True
        self.product.expiry_basis = "MFG"
        self.product.shelf_life_days = 100
        self.product.expiry_warning_days = 5
        self.product.save()
        self.stock.production_date = today - timedelta(days=101)
        self.stock.expiry_date = today - timedelta(days=1)
        self.stock.save()
        valid = InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.wh,
            product=self.product,
            location=self.loc,
            production_date=today - timedelta(days=95),
            expiry_date=today + timedelta(days=5),
            onhand_qty=7,
        )
        order = self.make_order(approval_status="OWNER_PENDING")
        item = order.lines.create(product=self.product, base_qty=3, base_price=10)
        order.owner_approve(by_user=self.manager, allow_backorder=False)
        self.stock.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(self.stock.allocated_qty, 1)
        self.assertEqual(valid.allocated_qty, 3)
        pick = WmsTaskLine.objects.get(src_model="OutboundOrderLine", src_id=item.pk)
        self.assertEqual(pick.plan_meta["inventory_detail_id"], valid.pk)
        self.assertEqual(pick.qty_plan, 3)
