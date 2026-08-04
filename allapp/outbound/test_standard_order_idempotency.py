from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import OutboundOrderViewSet
from allapp.products.models import Product, ProductUom


class StandardOrderIdempotencyTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="IDEM-OWN", name="Idempotency Owner")
        self.warehouse = Warehouse.objects.create(code="IDEM-WH", name="Idempotency WH")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.other_warehouse = Warehouse.objects.create(
            code="IDEM-WH2",
            name="Other Idempotency WH",
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
        )
        self.uom = ProductUom.objects.create(code="IDEM-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="IDEM-SKU",
            sku="IDEM-SKU",
            name="Idempotency Product",
            base_uom=self.uom,
            price=Decimal("10.00"),
            min_price=Decimal("1.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.user = self._salesperson("idem-salesperson")
        self.other_user = self._salesperson("idem-other-salesperson")
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="IDEM-CUSTOMER",
            name="Idempotency Customer",
        )
        self.other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="IDEM-CUSTOMER-2",
            name="Other Idempotency Customer",
        )
        self.other_product = Product.objects.create(
            owner=self.owner,
            code="IDEM-SKU-2",
            sku="IDEM-SKU-2",
            name="Other Idempotency Product",
            base_uom=self.uom,
            price=Decimal("11.00"),
            min_price=Decimal("1.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.factory = APIRequestFactory()
        self.view = OutboundOrderViewSet.as_view({"post": "create"})

    def _salesperson(self, username):
        user = get_user_model().objects.create_user(
            username=username,
            password="x",
            owner=self.owner,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="submit_outbound_as_owner_buyers",
            )
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        return user

    def payload(self, *, src_bill_no="", qty="1.000", price="10.0000"):
        return {
            "warehouse_id": self.warehouse.id,
            "customer_id": self.customer.id,
            "src_bill_no": src_bill_no,
            "remark": "幂等测试",
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": qty,
                    "price": price,
                }
            ],
        }

    def create(self, payload, *, key=None, user=None):
        extra = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        request = self.factory.post(
            "/api/outbound/orders/",
            payload,
            format="json",
            **extra,
        )
        force_authenticate(request, user=user or self.user)
        return self.view(request)

    def test_key_is_required_and_strictly_validated(self):
        for key in (None, "short", "contains space", " padded-key ", "x" * 65):
            with self.subTest(key=key):
                response = self.create(self.payload(), key=key)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("idempotency_key", response.data)
        self.assertFalse(OutboundOrder.objects.exists())

    def test_same_key_and_payload_replays_one_complete_order(self):
        key = "order-idempotency-0001"

        created = self.create(self.payload(), key=key)
        replayed = self.create(
            self.payload(qty="1.0", price="10.00"),
            key=key,
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertFalse(created.data["idempotent"])
        self.assertEqual(replayed.status_code, 200, replayed.data)
        self.assertTrue(replayed.data["idempotent"])
        self.assertTrue(replayed.data["replayed"])
        self.assertEqual(replayed.data["id"], created.data["id"])
        self.assertEqual(OutboundOrder.objects.count(), 1)
        order = OutboundOrder.objects.get()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.idempotency_key, key)
        self.assertEqual(len(order.idempotency_fingerprint), 64)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.order.create",
                object_id=str(order.id),
            ).count(),
            1,
        )

    def test_same_key_with_changed_payload_is_conflict(self):
        variants = {
            "customer": lambda payload: payload.update(
                customer_id=self.other_customer.id
            ),
            "warehouse": lambda payload: payload.update(
                warehouse_id=self.other_warehouse.id
            ),
            "product": lambda payload: payload["items"][0].update(
                product_id=self.other_product.id,
                price="11.0000",
            ),
            "quantity": lambda payload: payload["items"][0].update(qty="2.000"),
            "price": lambda payload: payload["items"][0].update(price="9.0000"),
            "recipient": lambda payload: payload.update(contact="changed recipient"),
        }

        for index, (field, mutate) in enumerate(variants.items(), start=1):
            with self.subTest(field=field):
                key = f"order-idempotency-change-{index:02d}"
                original_payload = self.payload()
                changed_payload = deepcopy(original_payload)
                mutate(changed_payload)

                created = self.create(original_payload, key=key)
                changed = self.create(changed_payload, key=key)

                self.assertEqual(created.status_code, 201, created.data)
                self.assertEqual(changed.status_code, 409, changed.data)

        self.assertEqual(OutboundOrder.objects.count(), len(variants))
        self.assertTrue(
            all(
                order.lines.get().base_qty == Decimal("1.000")
                for order in OutboundOrder.objects.all()
            )
        )

    def test_nonblank_source_number_is_unique_per_owner(self):
        created = self.create(
            self.payload(src_bill_no=" PLATFORM-001 "),
            key="order-source-unique-0001",
        )
        duplicate = self.create(
            self.payload(src_bill_no="PLATFORM-001"),
            key="order-source-unique-0002",
        )

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("平台单号重复", str(duplicate.data["src_bill_no"]))
        self.assertEqual(duplicate.data["existing_order_id"], str(created.data["id"]))
        self.assertEqual(OutboundOrder.objects.count(), 1)

    def test_soft_deleted_source_number_still_reports_the_original_order(self):
        created = self.create(
            self.payload(src_bill_no="HISTORICAL-SOURCE-001"),
            key="order-historical-source-0001",
        )
        OutboundOrder.all_objects.filter(pk=created.data["id"]).update(is_deleted=True)

        duplicate = self.create(
            self.payload(src_bill_no="HISTORICAL-SOURCE-001"),
            key="order-historical-source-0002",
        )

        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertEqual(duplicate.data["existing_order_id"], str(created.data["id"]))
        self.assertEqual(OutboundOrder.all_objects.count(), 1)

    def test_blank_source_numbers_with_different_keys_are_independent(self):
        first = self.create(self.payload(), key="order-blank-source-0001")
        second = self.create(self.payload(), key="order-blank-source-0002")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(OutboundOrder.objects.count(), 2)
        self.assertEqual(
            list(OutboundOrder.objects.values_list("src_bill_no", flat=True)),
            [None, None],
        )

    def test_same_key_is_scoped_to_the_authenticated_salesperson(self):
        key = "order-actor-scope-0001"
        first = self.create(self.payload(), key=key, user=self.user)
        second = self.create(self.payload(), key=key, user=self.other_user)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(OutboundOrder.objects.count(), 2)

    def test_audit_failure_rolls_back_order_and_lines(self):
        with mock.patch(
            "allapp.outbound.views.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.create(self.payload(), key="order-audit-rollback-0001")

        self.assertFalse(OutboundOrder.objects.exists())


class StandardOrderIdempotencyConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = Owner.objects.create(code="IDCONOWN", name="Concurrent Owner")
        self.warehouse = Warehouse.objects.create(
            code="IDCONWH", name="Concurrent WH"
        )
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        uom = ProductUom.objects.create(code="IDEM-CON-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="IDEM-CON-SKU",
            sku="IDEM-CON-SKU",
            name="Concurrent Product",
            base_uom=uom,
            price=Decimal("10.00"),
            min_price=Decimal("1.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.user = get_user_model().objects.create_user(
            username="idem-concurrent-salesperson",
            password="x",
            owner=self.owner,
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="submit_outbound_as_owner_buyers",
            )
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="IDEM-CON-CUSTOMER",
            name="Concurrent Customer",
        )

    def payload(self, src_bill_no=""):
        return {
            "warehouse_id": self.warehouse.id,
            "customer_id": self.customer.id,
            "src_bill_no": src_bill_no,
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "10.0000",
                }
            ],
        }

    def concurrent_create(self, *, key, payload, barrier):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/api/outbound/orders/",
                payload,
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
            )
            force_authenticate(request, user=self.user)
            barrier.wait(timeout=10)
            response = OutboundOrderViewSet.as_view({"post": "create"})(request)
            return response.status_code, dict(response.data)
        finally:
            close_old_connections()

    def test_concurrent_same_key_creates_exactly_one_order(self):
        barrier = threading.Barrier(2)
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self.concurrent_create,
                    key="order-concurrent-idem-0001",
                    payload=payload,
                    barrier=barrier,
                )
                for _ in range(2)
            ]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(OutboundOrder.objects.count(), 1)
        order = OutboundOrder.objects.get()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="outbound.order.create").count(), 1
        )

    def test_concurrent_same_source_with_different_keys_creates_one_order(self):
        barrier = threading.Barrier(2)
        payload = self.payload("CONCURRENT-SOURCE-001")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    self.concurrent_create,
                    key=f"order-concurrent-source-000{index}",
                    payload=payload,
                    barrier=barrier,
                )
                for index in (1, 2)
            ]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual(sorted(status for status, _ in results), [201, 400])
        duplicate_payload = next(data for status, data in results if status == 400)
        self.assertIn("平台单号重复", str(duplicate_payload["src_bill_no"]))
        self.assertEqual(OutboundOrder.objects.count(), 1)
        self.assertEqual(OutboundOrder.objects.get().lines.count(), 1)
