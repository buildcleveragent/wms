from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.core.choices import InvTxType, ZoneType
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Warehouse
from allapp.tasking.models import WmsTaskLine

from . import tests as pos_fixtures
from .models import PosSale
from .serializers import PosProductSerializer


class PosSaleZoneScopeTests(TestCase):
    open_shift = pos_fixtures.PosApiTests.open_shift

    def setUp(self):
        pos_fixtures.PosApiTests.setUp(self)
        self.storage = InventoryDetail.objects.get(product=self.product)
        self.storage.onhand_qty = Decimal("6")
        self.storage.batch_no = "STORAGE"
        self.storage.expiry_date = date(2031, 1, 1)
        self.storage.save()
        self.pick = self._stock_layer(ZoneType.PICK, "4", date(2030, 1, 1))
        self.returns = self._stock_layer(ZoneType.RETURN, "3", date(2029, 1, 1))

    def _stock_layer(self, zone_type, qty, expiry):
        location = Location.objects.create(
            warehouse=self.warehouse,
            code=f"SWPOS-02-01-{zone_type:02d}",
            zone_type=zone_type,
        )
        return InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            location=location,
            zone_type=zone_type,
            batch_no=zone_type.name,
            expiry_date=expiry,
            onhand_qty=Decimal(qty),
            base_unit=self.uom.code,
        )

    def _checkout(self, qty=1, **fields):
        return self.client.post(
            "/api/pos/checkout/",
            {
                "payment": {"method": "CASH", "amount_received": str(qty * 10)},
                "items": [{"product_id": self.product.pk, "qty": str(qty), "price": "10"}],
                **fields,
            },
            format="json",
        )

    def _lookup(self, **params):
        return self.client.get("/api/pos/products/", {"barcode": "POS-UNIT-BAR", **params})

    def _balances(self):
        return dict(
            InventoryDetail.objects.filter(product=self.product).values_list("id", "onhand_qty")
        )

    def test_catalog_and_serializer_share_default_and_explicit_scopes(self):
        OwnerWarehouseBinding.objects.get_or_create(owner=self.owner, warehouse=self.warehouse)
        for zone, expected in [(None, 10), (ZoneType.PICK, 4), (ZoneType.STORAGE, 6)]:
            with self.subTest(zone=zone):
                params = {} if zone is None else {"zone_type": zone}
                for lookup in (params, {**params, "include_out_of_stock": 1}):
                    found = self._lookup(**lookup)
                    self.assertEqual(found.status_code, 200, found.data)
                    self.assertEqual(Decimal(found.data["results"][0]["available_qty"]), expected)
                fallback = PosProductSerializer(
                    self.product, context={"warehouse_id": self.warehouse.pk, **params}
                )
                self.assertEqual(Decimal(fallback.data["available_qty"]), expected)

    def test_expiry_day_and_frozen_location_apply_to_catalog_and_checkout(self):
        from django.utils import timezone

        OwnerWarehouseBinding.objects.get_or_create(owner=self.owner, warehouse=self.warehouse)
        today = timezone.now().date()
        self.product.expiry_control = True
        self.product.expiry_basis = "MFG"
        self.product.shelf_life_days = 100
        self.product.expiry_warning_days = 5
        self.product.save()
        for layer, expiry in [(self.pick, today - timedelta(days=1)), (self.storage, today)]:
            layer.expiry_date = expiry
            layer.production_date = expiry - timedelta(days=100)
            layer.save()
        result = self._lookup(include_out_of_stock=1)
        self.assertEqual(Decimal(result.data["results"][0]["available_qty"]), 6)
        self.storage.location.is_frozen = True
        self.storage.location.save(update_fields=["is_frozen"])
        result = self._lookup(include_out_of_stock=1)
        self.assertEqual(Decimal(result.data["results"][0]["available_qty"]), 0)
        self.assertEqual(self._checkout().status_code, 400)
        self.assertFalse(
            InventoryTransaction.objects.filter(product=self.product, tx_type="ISSUE").exists()
        )
        self.storage.location.is_frozen = False
        self.storage.location.save(update_fields=["is_frozen"])
        result = self._checkout()
        self.assertEqual(result.status_code, 201, result.data)
        issued = InventoryTransaction.objects.get(product=self.product, tx_type="ISSUE")
        self.assertEqual(
            (issued.location_id, issued.expiry_date), (self.storage.location_id, today)
        )

    def test_checkout_splits_by_fefo_and_returns_to_original_layers(self):
        refused = self._checkout(11)
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertFalse(PosSale.objects.exists())
        self.assertFalse(InventoryTransaction.objects.exists())

        created = self._checkout(8, idempotency_key="two-zones")
        self.assertEqual(created.status_code, 201, created.data)
        sale = created.data["sale"]
        replay = self._checkout(8, idempotency_key="two-zones")
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data["sale"]["id"], sale["id"])
        self.assertEqual(
            self._balances(), {self.pick.pk: 0, self.storage.pk: 2, self.returns.pk: 3}
        )
        issued = InventoryTransaction.objects.filter(product=self.product, tx_type=InvTxType.ISSUE)
        self.assertCountEqual(
            issued.values_list("location_id", "batch_no", "qty_delta"),
            [
                (self.pick.location_id, "PICK", Decimal("-4")),
                (self.storage.location_id, "STORAGE", Decimal("-4")),
            ],
        )

        Location.objects.filter(pk__in=[self.pick.location_id, self.storage.location_id]).update(
            is_frozen=True
        )
        returned = self.client.post(
            "/api/pos/returns/",
            {
                "sale_id": sale["id"],
                "idempotency_key": "two-zones-return",
                "reason": "退回原销售批次",
                "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": "8"}],
                "refunds": [{"method": "CASH", "amount": "80"}],
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.data)
        self.assertEqual(
            self._balances(), {self.pick.pk: 4, self.storage.pk: 6, self.returns.pk: 3}
        )

    def test_earlier_storage_expiry_is_picked_first_and_void_restores_both_layers(self):
        self.storage.expiry_date = date(2029, 12, 1)
        self.storage.save()
        created = self._checkout(8)
        self.assertEqual(created.status_code, 201, created.data)
        sale = created.data["sale"]
        self.assertEqual(
            self._balances(), {self.pick.pk: 2, self.storage.pk: 0, self.returns.pk: 3}
        )
        lines = WmsTaskLine.objects.filter(task__source_app="pos", task__ref_no=sale["sale_no"])
        self.assertEqual(lines.count(), 2)
        for layer, qty in [(self.storage, Decimal("6")), (self.pick, Decimal("2"))]:
            with self.subTest(zone=layer.zone_type):
                line = lines.get(from_location_id=layer.location_id)
                self.assertEqual(line.qty_done, qty)
                self.assertEqual(line.plan_meta["source_inventory_detail_id"], layer.pk)
                tx = InventoryTransaction.objects.get(
                    src_model="WmsTask",
                    src_id=line.task_id,
                    src_line_id=line.pk,
                    tx_type=InvTxType.ISSUE,
                )
                self.assertEqual(tx.qty_delta, -qty)
                self.assertEqual(tx.location_id, layer.location_id)
                self.assertEqual(tx.batch_no, layer.batch_no)
                self.assertEqual(tx.expiry_date, layer.expiry_date)

        Location.objects.filter(pk__in=[self.pick.location_id, self.storage.location_id]).update(
            is_frozen=True
        )
        voided = self.client.post(
            f"/api/pos/sales/{sale['id']}/void/", {"reason": "撤销"}, format="json"
        )
        self.assertEqual(voided.status_code, 200, voided.data)
        self.assertEqual(
            self._balances(), {self.pick.pk: 4, self.storage.pk: 6, self.returns.pk: 3}
        )

    def test_excluded_zones_cannot_be_selected_for_new_sales(self):
        for zone in ZoneType:
            if zone in (ZoneType.PICK, ZoneType.STORAGE):
                continue
            with self.subTest(zone=zone):
                lookup = self._lookup(zone_type=zone)
                self.assertEqual(lookup.status_code, 400)
                self.assertIn("拣货区和存储区", lookup.data["detail"])
                failed = self._checkout(stock_zone_type=zone)
                self.assertEqual(failed.status_code, 400, failed.data)
                self.assertIn("stock_zone_type", failed.data)
        self.assertFalse(PosSale.objects.exists())
        self.assertFalse(InventoryTransaction.objects.exists())

    def test_historical_single_zone_sale_can_replay_and_void(self):
        request = {"stock_zone_type": ZoneType.RETURN, "idempotency_key": "pre-upgrade-sale"}
        # Before the dual-zone rule any valid zone could be passed at checkout.
        with patch("allapp.pos.services.POS_SALE_ZONE_TYPES", tuple(ZoneType)):
            created = self._checkout(**request)
        self.assertEqual(created.status_code, 201, created.data)
        sale_id = created.data["sale"]["id"]
        replay = self._checkout(**request)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data["sale"]["id"], sale_id)
        changed = self._checkout(2, **request)
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(
            self._balances(), {self.pick.pk: 4, self.storage.pk: 6, self.returns.pk: 2}
        )
        voided = self.client.post(
            f"/api/pos/sales/{sale_id}/void/", {"reason": "撤销"}, format="json"
        )
        self.assertEqual(voided.status_code, 200, voided.data)
        self.assertEqual(
            self._balances(), {self.pick.pk: 4, self.storage.pk: 6, self.returns.pk: 3}
        )

    def test_legacy_request_key_cannot_be_replayed_by_another_cashier_or_warehouse(self):
        request = {"idempotency_key": "shared-terminal-key"}
        created = self._checkout(**request)
        self.assertEqual(created.status_code, 201, created.data)
        sale = PosSale.objects.get()
        other_cashier = get_user_model().objects.create_user(
            username="other-zone-cashier", warehouse=self.warehouse
        )
        other_cashier.user_permissions.add(Permission.objects.get(codename="add_possale"))
        self.open_shift(other_cashier)
        self.client.force_authenticate(other_cashier)
        for fingerprint in (sale.idempotency_fingerprint, ""):
            with self.subTest(fingerprint_exists=bool(fingerprint)):
                PosSale.objects.filter(pk=sale.pk).update(idempotency_fingerprint=fingerprint)
                rejected = self._checkout(**request)
                self.assertEqual(rejected.status_code, 400, rejected.data)
                self.assertIn("idempotency_key", rejected.data)

        self.user.warehouse = Warehouse.objects.create(code="POS-MOVED", name="Other Warehouse")
        self.user.save(update_fields=["warehouse"])
        self.client.force_authenticate(self.user)
        rejected = self._checkout(**request)
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn("idempotency_key", rejected.data)
        self.assertEqual(PosSale.objects.count(), 1)
        self.assertEqual(
            self._balances(), {self.pick.pk: 3, self.storage.pk: 6, self.returns.pk: 3}
        )
