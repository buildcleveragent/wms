from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from allapp.baseinfo.customer_access import customer_binding, customers_for_owner
from allapp.baseinfo.models import Owner, OwnerCustomerBinding, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail, InventoryTransaction, PostingJournal
from allapp.outbound import services
from allapp.outbound import test_assisted_flow as fixtures
from allapp.outbound.export_print import pick_task_print
from allapp.outbound.models import OutboundOrder
from allapp.outbound.transfer_models import (
    AssistedOwnershipTransfer,
    AssistedTransferReservation,
    OwnerProductMapping,
)
from allapp.outbound.views import AssistedOutboundOrderViewSet, PickTaskViewSet
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import Product, ProductPackage, ProductUom
from allapp.tasking.models import WmsTask
from allapp.tasking.services import adjust_pick_line_qty, scan_task


class TransferFixtures:
    _request = fixtures.AssistedOutboundFlowTests._request
    _create = fixtures.AssistedOutboundFlowTests._create

    def setUp(self):
        fixtures.AssistedOutboundFlowTests.setUp(self)
        self.target_owner = Owner.objects.create(
            name="新销售货主", code="NEW-OWN", allow_warehouse_assisted_outbound=True
        )
        OwnerWarehouseBinding.objects.create(owner=self.target_owner, warehouse=self.warehouse)
        self.target_binding = OwnerCustomerBinding.objects.create(
            owner=self.target_owner,
            customer=self.customer,
            code="NEW-CUSTOMER",
            salesperson=self.operator,
        )
        self.detail.onhand_qty = Decimal("100")
        self.detail.save()

    def payload(self, **overrides):
        return {
            "request_id": str(uuid4()),
            "owner_id": self.owner.pk,
            "new_owner_id": self.target_owner.pk,
            "customer_id": self.customer.pk,
            "items": [
                {"product_id": self.product.pk, "qty": "10", "price": "12", "transfer_price": "8"}
            ],
            **overrides,
        }

    def create_order(self, payload=None):
        response = self._create(payload or self.payload())
        self.assertEqual(response.status_code, 201, response.data)
        return OutboundOrder.objects.get(pk=response.data["order_id"]), WmsTask.objects.get(
            pk=response.data["task_id"]
        )

    def review(self, task):
        response = PickTaskViewSet.as_view({"post": "create_review_task"})(
            self._request("post", "/review/"), pk=task.pk
        )
        self.assertEqual(response.status_code, 200, response.data)

    def post(self, task):
        return PickTaskViewSet.as_view({"post": "post"})(
            self._request("post", "/post/"), pk=task.pk
        )

    def complete(self, task):
        for line in task.lines.all():
            adjust_pick_line_qty(
                task.pk, line.pk, line.qty_plan, by_user=self.operator, client_seq=str(line.pk)
            )
        self.review(task)
        response = self.post(task)
        self.assertEqual(response.status_code, 200, response.data)


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce", INVENTORY_FIFO_ENABLED=False)
class OwnershipTransferTests(TransferFixtures, TestCase):
    def test_customer_shared_id_with_separate_codes_and_inactive_relationship(self):
        self.assertEqual(customers_for_owner(self.owner.pk).get().pk, self.customer.pk)
        self.assertEqual(
            customers_for_owner(self.target_owner.pk).get().owner_customer_code, "NEW-CUSTOMER"
        )
        self.target_binding.is_active = False
        self.target_binding.save()
        self.assertIsNone(customer_binding(self.customer, self.target_owner.pk))
        self.assertTrue(customers_for_owner(self.owner.pk).exists())
        response = self._create(self.payload())
        self.assertEqual(response.status_code, 400)

    def test_mysql_rejects_unpaired_or_wrong_sign_ownership_transactions(self):
        for tx_type, quantity, pair_id in (
            ("OWNER_XFER_OUT", -1, None),
            ("OWNER_XFER_IN", 1, None),
            ("OWNER_XFER_OUT", 1, uuid4()),
            ("OWNER_XFER_IN", -1, uuid4()),
        ):
            with self.subTest(tx_type=tx_type, quantity=quantity, paired=bool(pair_id)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    InventoryTransaction.objects.bulk_create(
                        [
                            InventoryTransaction(
                                owner=self.owner,
                                warehouse=self.warehouse,
                                product=self.product,
                                location=self.location,
                                tx_type=tx_type,
                                qty_delta=quantity,
                                pair_id=pair_id,
                                src_model="InvalidTransfer",
                                src_id=1,
                                src_line_id=1,
                            )
                        ]
                    )

    def test_atomic_three_legs_preserve_existing_target_stock_and_reservations(self):
        target_product = Product.objects.create(
            owner=self.target_owner,
            code="TARGET",
            name=self.product.name,
            base_uom=self.uom,
            volume=self.product.volume,
        )
        OwnerProductMapping.objects.create(
            source_product=self.product,
            target_owner=self.target_owner,
            target_product=target_product,
        )
        target_detail = InventoryDetail.objects.create(
            owner=self.target_owner,
            product=target_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=20,
            allocated_qty=5,
        )
        order, task = self.create_order()
        self.assertEqual(order.owner_id, self.target_owner.pk)
        self.assertEqual(task.owner_id, self.target_owner.pk)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 100)
        self.assertEqual(self.detail.allocated_qty, 10)
        with mock.patch("allapp.billing.services.accrue_for_posting") as accrue:
            self.complete(task)
            self.assertEqual(accrue.call_count, 1)
            self.assertEqual(accrue.call_args.args[0].owner_id, self.target_owner.pk)
            replay = self.post(task)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(accrue.call_count, 1)
        self.detail.refresh_from_db()
        target_detail.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 90)
        self.assertEqual(self.detail.allocated_qty, 0)
        self.assertEqual(target_detail.onhand_qty, 20)
        self.assertEqual(target_detail.allocated_qty, 5)
        self.assertTrue(order.is_closed)
        txs = InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk)
        self.assertEqual(txs.count(), 3)
        self.assertEqual(txs.get(tx_type="OWNER_XFER_OUT").qty_delta, -10)
        self.assertEqual(txs.get(tx_type="OWNER_XFER_IN").qty_delta, 10)
        self.assertEqual(txs.get(tx_type="ISSUE").qty_delta, -10)
        self.assertEqual(txs.get(tx_type="ISSUE").src_no, order.order_no)
        self.assertEqual(
            txs.get(tx_type="OWNER_XFER_OUT").src_no, order.assisted_transfer.transfer_no
        )
        self.assertEqual(
            txs.get(tx_type="OWNER_XFER_OUT").pair_id, txs.get(tx_type="OWNER_XFER_IN").pair_id
        )
        self.assertEqual(order.assisted_transfer.status, "POSTED")
        from allapp.reports.etl_operations import sync_dimensions, sync_inventory_transactions
        from allapp.reports.models import FactInventoryTxn

        sync_dimensions()
        sync_inventory_transactions(txs)
        self.assertEqual(FactInventoryTxn.objects.filter(order_type="TRANSFER").count(), 2)
        physical_outbound = FactInventoryTxn.objects.get(order_type="OUTBOUND")
        self.assertEqual(physical_outbound.qty_delta, -10)
        self.assertEqual(physical_outbound.owner.owner_id, self.target_owner.pk)
        self.assertEqual(physical_outbound.order_id, order.pk)

    def test_auto_clone_scan_original_code_print_and_idempotency(self):
        self.target_owner.phone = "13912345678"
        self.target_owner.save()
        payload = self.payload()
        order, task = self.create_order(payload)
        sales_product = order.lines.get().product
        self.assertNotEqual(sales_product.pk, self.product.pk)
        self.assertEqual(sales_product.owner_id, self.target_owner.pk)
        scan = scan_task(task.pk, self.product.sku, 10, by_user=self.operator, client_seq="once")
        again = scan_task(task.pk, self.product.sku, 10, by_user=self.operator, client_seq="once")
        self.assertTrue(again["idempotent"])
        self.assertEqual(scan["line_id"], again["line_id"])
        replay = self._create(payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        changed = {**payload, "items": [{**payload["items"][0], "transfer_price": "9"}]}
        self.assertEqual(self._create(changed).status_code, 409)
        self.assertEqual(self._create({**payload, "new_owner_id": None}).status_code, 409)
        print_request = self.factory.get(f"/api/outbound/pda/pick-tasks/{task.pk}/print/")
        print_request.user = self.operator
        printed = pick_task_print(print_request, task.pk)
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, self.target_owner.name)
        self.assertContains(printed, self.target_owner.phone)
        self.assertContains(printed, "12.00")
        self.review(task)
        self.assertEqual(self.post(task).status_code, 200)

    def test_shortage_rolls_back_cloned_product_order_mapping_and_reservation(self):
        payload = self.payload(items=[{"product_id": self.product.pk, "qty": "101", "price": "12"}])
        before_products = Product.objects.count()
        response = self._create(payload)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Product.objects.count(), before_products)
        self.assertFalse(AssistedOwnershipTransfer.objects.exists())
        self.assertFalse(OwnerProductMapping.objects.exists())
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, 0)

    def test_cancel_releases_source_without_any_stock_transfer(self):
        self.detail.allocated_qty = 7
        self.detail.save()
        order, task = self.create_order()
        self.assertEqual(services.unallocate_for_order(order, by_user=self.operator), 10)
        self.assertEqual(services.unallocate_for_order(order, by_user=self.operator), 0)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 100)
        self.assertEqual(self.detail.allocated_qty, 7)
        self.assertEqual(AssistedTransferReservation.objects.get().status, "RELEASED")
        self.assertFalse(InventoryTransaction.objects.filter(src_id=task.pk).exists())

    def test_failed_second_leg_rolls_back_all_stock_and_retries(self):
        order, task = self.create_order()
        line = task.lines.get()
        adjust_pick_line_qty(task.pk, line.pk, 10, by_user=self.operator)
        self.review(task)
        create_tx = InventoryTransaction.objects.create

        def failing_create(**kwargs):
            if kwargs["tx_type"] == "OWNER_XFER_IN":
                raise RuntimeError("injected transfer failure")
            return create_tx(**kwargs)

        with mock.patch.object(InventoryTransaction.objects, "create", side_effect=failing_create):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.post(task)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 100)
        self.assertEqual(self.detail.allocated_qty, 10)
        self.assertFalse(InventoryTransaction.objects.filter(src_id=task.pk).exists())
        self.assertEqual(
            PostingJournal.objects.get(src_model="WmsTask", src_id=task.pk).status, "FAILED"
        )
        self.assertEqual(AssistedOwnershipTransfer.objects.get().status, "RESERVED")
        self.assertEqual(self.post(task).status_code, 200)
        self.assertEqual(InventoryTransaction.objects.filter(src_id=task.pk).count(), 3)

    def test_target_owner_authorization_required_and_same_owner_rejected(self):
        self.assertEqual(self._create(self.payload(new_owner_id=self.owner.pk)).status_code, 400)
        OwnerWarehouseBinding.objects.filter(owner=self.target_owner).update(is_active=False)
        self.assertEqual(self._create(self.payload()).status_code, 403)

    def test_default_transfer_price_matches_sale_price(self):
        order, _ = self.create_order(
            self.payload(items=[{"product_id": self.product.pk, "qty": "10", "price": "7"}])
        )
        self.assertEqual(order.assisted_transfer.lines.get().transfer_price, Decimal("7"))

    def test_work_fee_is_charged_to_target_once_without_transfer_receipt_fee(self):
        from allapp.billing.enums import CalcMethod, ChargeType
        from allapp.billing.models import BillingAccrual, BillingEvent, BillingRule

        for owner in (self.owner, self.target_owner):
            for charge_type in (ChargeType.PICK, ChargeType.RECEIVE):
                BillingRule.objects.create(
                    owner=owner,
                    warehouse=self.warehouse,
                    charge_type=charge_type,
                    calc_method=CalcMethod.PER_QTY_ABSDEL,
                    unit_price=Decimal("2"),
                )
        _, task = self.create_order()
        self.complete(task)
        self.assertEqual(self.post(task).status_code, 200)
        fee = BillingAccrual.objects.get(charge_type=ChargeType.PICK)
        self.assertEqual((fee.owner_id, fee.quantity, fee.amount), (self.target_owner.pk, 10, 20))
        self.assertFalse(BillingEvent.objects.filter(owner=self.owner).exists())
        self.assertFalse(BillingEvent.objects.filter(charge_type=ChargeType.RECEIVE).exists())

    def test_source_package_mapping_preserves_base_conversion(self):
        carton = ProductUom.objects.create(code="XFER-BOX", name="箱")
        package = ProductPackage.objects.create(product=self.product, uom=carton, qty_in_base=5)
        order, task = self.create_order(
            self.payload(
                items=[
                    {
                        "product_id": self.product.pk,
                        "qty": "10",
                        "package_id": package.pk,
                        "package_qty": "2",
                        "price": "4",
                    }
                ]
            )
        )
        sales_line = order.lines.get()
        self.assertNotEqual(sales_line.aux_uom_id, package.pk)
        self.assertEqual(sales_line.aux_uom.product_id, sales_line.product_id)
        self.assertEqual(sales_line.aux_uom.qty_in_base, 5)
        self.complete(task)

    def test_exact_barcode_match_is_reused_and_incompatible_spec_is_rejected(self):
        add_product_barcode(product=self.product, barcode="XFER-SAME", barcode_type="UNIT")
        target = Product.objects.create(
            owner=self.target_owner,
            code="BARCODE",
            name="existing",
            base_uom=self.uom,
            volume=self.product.volume,
            spec="different",
        )
        add_product_barcode(product=target, barcode="XFER-SAME", barcode_type="UNIT")
        response = self._create(self.payload())
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(OwnerProductMapping.objects.exists())
        self.assertFalse(OutboundOrder.objects.exists())
        target.spec = self.product.spec
        target.save()
        order, _ = self.create_order()
        self.assertEqual(order.lines.get().product_id, target.pk)
        target.refresh_from_db()
        self.assertEqual(target.name, "existing")

    def test_conflicting_package_mapping_is_rejected_atomically(self):
        box = ProductUom.objects.create(code="CONFLICT-BOX", name="箱")
        package = ProductPackage.objects.create(product=self.product, uom=box, qty_in_base=5)
        target = Product.objects.create(
            owner=self.target_owner,
            code="CONFLICT",
            name="target",
            base_uom=self.uom,
            volume=self.product.volume,
        )
        ProductPackage.objects.create(product=target, uom=box, qty_in_base=6)
        OwnerProductMapping.objects.create(
            source_product=self.product, target_owner=self.target_owner, target_product=target
        )
        response = self._create(
            self.payload(
                items=[
                    {
                        "product_id": self.product.pk,
                        "qty": "10",
                        "package_id": package.pk,
                        "package_qty": "2",
                        "price": "12",
                    }
                ]
            )
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, 0)

    def test_two_lots_reserve_exact_rows_and_scan_supplied_lot(self):
        self.product.batch_control = True
        self.product.expiry_control = True
        self.product.save()
        self.detail.batch_no = "FIRST"
        self.detail.production_date = date(2026, 1, 1)
        self.detail.expiry_date = date(2027, 1, 1)
        self.detail.onhand_qty = Decimal("4.0005")
        self.detail.save()
        second = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            batch_no="SECOND",
            production_date=date(2026, 2, 1),
            expiry_date=date(2027, 2, 1),
            onhand_qty=Decimal("95.9995"),
        )
        _, task = self.create_order()
        self.assertEqual(task.lines.count(), 2)
        # The second lot must select its own reservation even though FIRST sorts earlier.
        with mock.patch(
            "allapp.tasking.services._resolver",
            return_value=lambda *_: {
                "product_id": self.product.pk,
                "pack_qty": 1,
                "code_type": "UNIT",
                "lot_no": "SECOND",
            },
        ):
            scan_task(task.pk, "LOT-SECOND", 6, by_user=self.operator, client_seq="second")
        with mock.patch(
            "allapp.tasking.services._resolver",
            return_value=lambda *_: {
                "product_id": self.product.pk,
                "pack_qty": 1,
                "code_type": "UNIT",
                "lot_no": "WRONG",
            },
        ):
            with self.assertRaises(ValidationError):
                scan_task(task.pk, "LOT-WRONG", 4, by_user=self.operator)
        first_line = task.lines.filter(qty_done=0).get()
        adjust_pick_line_qty(task.pk, first_line.pk, 4, by_user=self.operator)
        self.review(task)
        self.assertEqual(self.post(task).status_code, 200)
        self.detail.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            (self.detail.onhand_qty, second.onhand_qty),
            (Decimal("0.0005"), Decimal("89.9995")),
        )
        self.assertEqual((self.detail.allocated_qty, second.allocated_qty), (0, 0))
        self.assertEqual(InventoryTransaction.objects.filter(src_id=task.pk).count(), 6)

    def test_serial_stock_preserves_source_private_container(self):
        from allapp.locations.models import Container

        self.product.serial_control = True
        self.product.save()
        container = Container.objects.create(
            container_no="XFER-PRIVATE",
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            scope=Container.Scope.PRIVATE,
        )
        self.detail.serial_no = "SERIAL-ONE"
        self.detail.onhand_qty = 1
        self.detail.container = container
        self.detail.save()
        order, task = self.create_order(
            self.payload(
                items=[
                    {
                        "product_id": self.product.pk,
                        "qty": "1",
                        "price": "12",
                    }
                ]
            )
        )
        self.complete(task)
        container.refresh_from_db()
        self.assertEqual(container.owner_id, self.owner.pk)
        target_detail = InventoryDetail.objects.get(owner=self.target_owner)
        self.assertIsNone(target_detail.container_id)
        self.assertEqual(target_detail.serial_no, "SERIAL-ONE")
        self.assertEqual(target_detail.onhand_qty, 0)
        self.assertEqual(
            order.assisted_transfer.lines.get().reservations.get().source_snapshot["container_id"],
            container.pk,
        )

    def test_partial_transfer_and_started_cancellation_are_rejected(self):
        order, task = self.create_order()
        adjust_pick_line_qty(task.pk, task.lines.get().pk, 5, by_user=self.operator)
        with self.assertRaises(ValidationError):
            services.unallocate_for_order(order, by_user=self.operator)
        response = PickTaskViewSet.as_view({"post": "create_review_task"})(
            self._request("post", "/review/"), pk=task.pk
        )
        self.assertEqual(response.status_code, 400)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 100)

    def test_history_search_returns_one_shared_customer_order_with_full_chain(self):
        order, _ = self.create_order()
        view = AssistedOutboundOrderViewSet.as_view({"get": "history"})
        response = view(self._request("get", "/history/", {"search": self.customer.name}))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["source_owner"]["id"], self.owner.pk)
        self.assertEqual(row["owner"]["id"], self.target_owner.pk)
        self.assertEqual(row["transfer_no"], order.assisted_transfer.transfer_no)
        self.assertEqual(row["customer"]["code"], "NEW-CUSTOMER")

    @override_settings(INVENTORY_FIFO_ENABLED=True)
    def test_fifo_receipt_cost_and_exact_new_layer_consumption(self):
        from allapp.inventory.fifo import create_receipt_layer
        from allapp.inventory.models import InventoryCostLayer, InventoryLayerPosition

        create_receipt_layer(
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            product_id=self.product.pk,
            location_id=self.location.pk,
            quantity=100,
            source_type="TEST",
            source_id=1,
            unit_cost=Decimal("3"),
            cost_currency="CNY",
        )
        target = Product.objects.create(
            owner=self.target_owner,
            code="FIFO-TARGET",
            name="fifo",
            base_uom=self.uom,
            volume=self.product.volume,
        )
        OwnerProductMapping.objects.create(
            source_product=self.product, target_owner=self.target_owner, target_product=target
        )
        InventoryDetail.objects.create(
            owner=self.target_owner,
            product=target,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=20,
            allocated_qty=5,
        )
        old_layer, _ = create_receipt_layer(
            owner_id=self.target_owner.pk,
            warehouse_id=self.warehouse.pk,
            product_id=target.pk,
            location_id=self.location.pk,
            quantity=20,
            source_type="TEST",
            source_id=2,
            unit_cost=Decimal("2"),
            cost_currency="CNY",
            received_date=date(2020, 1, 1),
        )
        order, task = self.create_order()
        self.complete(task)
        received = (
            InventoryCostLayer.objects.filter(owner=self.target_owner)
            .exclude(pk=old_layer.pk)
            .get()
        )
        self.assertEqual(received.unit_cost, 8)
        self.assertEqual(InventoryLayerPosition.objects.get(layer=received).remaining_qty, 0)
        self.assertEqual(
            InventoryLayerPosition.objects.get(layer__owner=self.owner).remaining_qty, 90
        )
        self.assertEqual(InventoryLayerPosition.objects.get(layer=old_layer).remaining_qty, 20)


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce", INVENTORY_FIFO_ENABLED=False)
class TransferConcurrencyTests(TransferFixtures, TransactionTestCase):
    def concurrent(self, operation, values):
        barrier = Barrier(len(values))

        def run(value):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = operation(value)
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(values)) as pool:
            futures = [pool.submit(run, value) for value in values]
            return [future.result(timeout=60) for future in futures]

    def test_two_orders_cannot_reserve_more_than_source_stock(self):
        payloads = [
            self.payload(items=[{"product_id": self.product.pk, "qty": "60", "price": "12"}])
            for _ in range(2)
        ]
        responses = self.concurrent(self._create, payloads)
        self.assertEqual(sorted(status for status, _ in responses), [201, 400], responses)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.allocated_qty, 60)
        self.assertEqual(OutboundOrder.objects.count(), 1)

    def test_same_request_and_post_concurrently_have_one_business_effect(self):
        payload = self.payload()
        responses = self.concurrent(self._create, [payload, payload])
        self.assertEqual(sorted(status for status, _ in responses), [200, 201], responses)
        self.assertEqual(AssistedOwnershipTransfer.objects.count(), 1)
        task = WmsTask.objects.get(task_type="PICK")
        adjust_pick_line_qty(task.pk, task.lines.get().pk, 10, by_user=self.operator)
        self.review(task)
        responses = self.concurrent(self.post, [task, task])
        self.assertEqual([status for status, _ in responses], [200, 200], responses)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 90)
        self.assertEqual(InventoryTransaction.objects.filter(src_id=task.pk).count(), 3)


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class SharedCustomerIntegrationTests(TransferFixtures, TestCase):
    def salesperson(self, name, owner):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from allapp.accounts.models import UserRoleScope

        user = get_user_model().objects.create_user(username=name, password="x", owner=owner)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="submit_outbound_as_owner_buyers"
            )
        )
        UserRoleScope.objects.create(
            user=user, owner=owner, role=UserRoleScope.Role.OWNER_SALESPERSON
        )
        return user

    def test_standard_sales_and_catalog_use_owner_relationship_and_salesperson(self):
        from allapp.outbound.views import CustomerViewSet, OutboundOrderViewSet

        seller = self.salesperson("target-seller", self.target_owner)
        other = self.salesperson("other-seller", self.target_owner)
        self.target_binding.salesperson = seller
        self.target_binding.save()
        target = Product.objects.create(
            owner=self.target_owner,
            code="SHARED-SALE",
            name="target",
            base_uom=self.uom,
            volume=self.product.volume,
        )
        catalog = CustomerViewSet.as_view({"get": "list"})
        response = catalog(
            self._request("get", "/customers/", {"owner_id": self.target_owner.pk}, user=seller)
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["results"],
            [{"id": self.customer.pk, "name": self.customer.name, "code": "NEW-CUSTOMER"}],
        )
        hidden = catalog(self._request("get", "/customers/", user=other))
        self.assertEqual(hidden.data["results"], [])
        payload = {
            "warehouse_id": self.warehouse.pk,
            "customer_id": self.customer.pk,
            "items": [{"product_id": target.pk, "qty": "1", "price": "12"}],
        }
        view = OutboundOrderViewSet.as_view({"post": "create"})
        denied = self._request("post", "/orders/", payload, user=other)
        denied.META["HTTP_IDEMPOTENCY_KEY"] = "shared-denied-001"
        self.assertEqual(view(denied).status_code, 400)
        request = self._request("post", "/orders/", payload, user=seller)
        request.META["HTTP_IDEMPOTENCY_KEY"] = "shared-allowed-001"
        response = view(request)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(OutboundOrder.objects.get().customer_id, self.customer.pk)
        self.assertEqual(self.customer.owner_id, self.owner.pk)

    def test_report_customer_dimensions_are_per_owner_and_codes_do_not_leak(self):
        from allapp.reports.etl_operations import sync_dimensions, sync_outbound_facts
        from allapp.reports.models import CustomerDim, FactOutboundLine

        order, _ = self.create_order()
        sync_dimensions()
        self.assertEqual(
            CustomerDim.objects.filter(customer_id=self.customer.pk, is_current=True).count(), 2
        )
        self.assertEqual(
            CustomerDim.objects.get(owner_id=self.owner.pk, customer_id=self.customer.pk).code,
            self.customer.code,
        )
        self.assertEqual(
            CustomerDim.objects.get(
                owner_id=self.target_owner.pk, customer_id=self.customer.pk
            ).code,
            "NEW-CUSTOMER",
        )
        sync_outbound_facts(OutboundOrder.objects.filter(pk=order.pk))
        fact = FactOutboundLine.objects.get(order_id=order.pk)
        self.assertEqual(fact.customer.owner_id, self.target_owner.pk)

    def test_source_owner_can_view_transfer_but_cannot_print_target_sale(self):
        from django.contrib import admin
        from django.contrib.auth.models import Permission

        from allapp.outbound.transfer_admin import (
            AssistedOwnershipTransferAdmin,
            TransferLineInline,
        )

        user = self.salesperson("source-seller", self.owner)
        user.is_staff = True
        user.save()
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="view_assistedownershiptransfer"
            )
        )
        order, task = self.create_order()
        request = self.factory.get("/admin/outbound/assistedownershiptransfer/")
        request.user = user
        model_admin = AssistedOwnershipTransferAdmin(AssistedOwnershipTransfer, admin.site)
        self.assertTrue(
            model_admin.get_queryset(request).filter(pk=order.assisted_transfer.pk).exists()
        )
        self.assertNotIn("sales_order", model_admin.get_fields(request, order.assisted_transfer))
        inline = TransferLineInline(AssistedOwnershipTransfer, admin.site)
        self.assertTrue(inline.has_view_permission(request, order.assisted_transfer))
        self.assertNotIn("sales_line", inline.get_fields(request, order.assisted_transfer))
        print_request = self.factory.get("/print/")
        print_request.user = user
        from django.core.exceptions import PermissionDenied
        from django.http import Http404

        try:
            result = pick_task_print(print_request, task.pk)
            self.assertIn(result.status_code, (403, 404))
        except (PermissionDenied, Http404):
            pass

    def test_admin_shared_identity_is_readonly_and_only_current_relationship_is_visible(self):
        from django.contrib import admin
        from django.contrib.auth.models import Permission

        from allapp.baseinfo.admin import CustomerAdmin, OwnerCustomerBindingInline
        from allapp.baseinfo.models import Customer

        user = self.salesperson("shared-admin", self.target_owner)
        user.is_staff = True
        user.save()
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="baseinfo",
                codename__in=["view_customer", "change_customer"],
            )
        )
        request = self.factory.get("/admin/baseinfo/customer/")
        request.user = user
        model_admin = CustomerAdmin(Customer, admin.site)
        self.assertTrue(model_admin.get_queryset(request).filter(pk=self.customer.pk).exists())
        self.assertEqual(
            set(model_admin.get_readonly_fields(request, self.customer)),
            set(model_admin.common_fields),
        )
        inline = OwnerCustomerBindingInline(Customer, admin.site)
        self.assertEqual(list(inline.get_queryset(request)), [self.target_binding])
        self.assertFalse(inline.has_add_permission(request, self.customer))
        self.assertIn("owner", inline.get_readonly_fields(request, self.customer))
        self.target_binding.is_active = False
        self.target_binding.save()
        self.assertTrue(inline.get_queryset(request).filter(pk=self.target_binding.pk).exists())
        self.client.force_login(user)
        response = self.client.get(f"/admin/baseinfo/customer/{self.customer.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NEW-CUSTOMER")
        self.assertNotContains(response, f'value="{self.customer.code}"')

    def test_mall_uses_shared_identity_and_disabled_relationship_does_not_disable_other_owner(self):
        from rest_framework.exceptions import PermissionDenied

        from allapp.salesapp.models import MiniProgramUser
        from allapp.salesapp.salemini_api import _buyer_bindings_for_user, _customer_for_context

        source_buyer = MiniProgramUser.objects.create(
            owner=self.owner, customer=self.customer, user=self.operator
        )
        target_buyer = MiniProgramUser.objects.create(
            owner=self.target_owner, customer=self.customer, user=self.operator
        )
        customer, buyer = _customer_for_context(self.target_owner, self.operator)
        self.assertEqual((customer.pk, buyer.pk), (self.customer.pk, target_buyer.pk))
        self.assertEqual(_buyer_bindings_for_user(self.operator).count(), 2)
        self.target_binding.is_active = False
        self.target_binding.save()
        with self.assertRaises(PermissionDenied):
            _customer_for_context(self.target_owner, self.operator)
        self.assertEqual(list(_buyer_bindings_for_user(self.operator)), [source_buyer])
        self.assertEqual(_customer_for_context(self.owner, self.operator)[0].pk, self.customer.pk)

    def test_relationship_uniqueness_and_legacy_creation_sync(self):
        from allapp.baseinfo.models import Customer

        self.assertEqual(OwnerCustomerBinding.objects.filter(customer=self.customer).count(), 2)
        with self.assertRaises(ValidationError):
            OwnerCustomerBinding.objects.create(
                owner=self.target_owner,
                customer=self.customer,
                code="ANOTHER",
                salesperson=self.operator,
            )
        second = Customer.objects.create(
            owner=self.owner, code="SECOND", name="同名不会合并", salesperson=self.operator
        )
        with self.assertRaises(ValidationError):
            OwnerCustomerBinding.objects.create(
                owner=self.target_owner,
                customer=second,
                code="NEW-CUSTOMER",
                salesperson=self.operator,
            )
        self.target_binding.external_code = "EXT"
        self.target_binding.save()
        with self.assertRaises(ValidationError):
            OwnerCustomerBinding.objects.create(
                owner=self.target_owner,
                customer=second,
                code="SECOND",
                external_code="EXT",
                salesperson=self.operator,
            )
        self.target_binding.code = "INDEPENDENT"
        self.target_binding.save()
        self.customer.name = "共享名字"
        self.customer.save()
        self.target_binding.refresh_from_db()
        self.assertEqual(self.target_binding.code, "INDEPENDENT")

    def test_primary_relationship_code_can_be_reused_after_renaming(self):
        from allapp.baseinfo.models import Customer

        original_code = self.customer.code
        binding = customer_binding(self.customer, self.owner.pk)
        binding.code = "RENAMED"
        binding.save()
        replacement = Customer.objects.create(
            owner=self.owner, code=original_code, name="新客户档案", salesperson=self.operator
        )
        self.assertNotEqual(replacement.pk, self.customer.pk)
        self.assertEqual(customer_binding(replacement, self.owner.pk).code, original_code)
