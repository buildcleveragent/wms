import importlib
import threading

from django.apps import apps
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Owner
from allapp.tasking.plugins.barcodes import default_resolver

from .models import Product, ProductIdentifierRegistry, ProductPackage, ProductUom


class ProductIdentifierRegistryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom = ProductUom.objects.create(code="RID-EA", name="件")
        cls.carton_uom = ProductUom.objects.create(code="RID-CTN", name="箱")
        cls.owner = Owner.objects.create(code="RID", name="注册表货主")

    def create_product(self, code, *, owner=None, **identifiers):
        return Product.objects.create(
            owner=owner or self.owner,
            code=code,
            name=code,
            base_uom=self.uom,
            expiry_control=False,
            expiry_basis=None,
            **identifiers,
        )

    def test_different_fields_cannot_claim_another_products_identifier(self):
        self.create_product("SHARED")

        for field in ("gtin", "unit_barcode", "external_code"):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_product(f"OTHER-{field}", **{field: " shared "})

    def test_generated_sku_skips_value_reserved_by_another_field(self):
        self.create_product("RID-2")

        product = self.create_product("SECOND")

        self.owner.refresh_from_db()
        self.assertEqual(product.sku, "RID-3")
        self.assertEqual(self.owner.next_sku_sequence, 4)

    def test_code_cannot_reuse_another_products_generated_sku(self):
        first = self.create_product("FIRST")

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_product(first.sku.lower())

    def test_same_product_duplicate_values_use_one_registry_row(self):
        product = self.create_product("RID-1")

        self.assertEqual(product.code, product.sku)
        self.assertEqual(
            ProductIdentifierRegistry.objects.filter(product=product).count(), 1
        )

    def test_data_migration_backfill_consolidates_same_product_values(self):
        product = self.create_product("RID-1")
        ProductIdentifierRegistry.objects.all().delete()
        migration = importlib.import_module(
            "allapp.products.migrations.0006_productidentifierregistry"
        )

        migration.backfill_identifier_registry(apps, None)

        self.assertEqual(
            ProductIdentifierRegistry.objects.filter(product=product).count(), 1
        )

    def test_same_value_is_allowed_for_different_owners(self):
        other_owner = Owner.objects.create(code="OTHER", name="其他货主")
        first = self.create_product("COMMON")
        second = self.create_product("COMMON", owner=other_owner)

        self.assertNotEqual(first.owner_id, second.owner_id)

    def test_soft_deleted_product_keeps_identifier_reserved(self):
        product = self.create_product("RESERVED")
        Product.all_objects.filter(pk=product.pk).update(is_deleted=True)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_product(" reserved ")

    def test_identifier_update_synchronizes_registry(self):
        product = self.create_product("UPDATE", unit_barcode="OLD-BARCODE")
        product.unit_barcode = "NEW-BARCODE"
        product.save(update_fields=["unit_barcode"])

        values = set(
            ProductIdentifierRegistry.objects.filter(product=product).values_list(
                "normalized_value", flat=True
            )
        )
        self.assertIn("NEW-BARCODE", values)
        self.assertNotIn("OLD-BARCODE", values)

    def test_bulk_and_direct_identifier_updates_are_guarded(self):
        product = self.create_product("GUARDED")

        with self.assertRaisesMessage(ValueError, "QuerySet.update"):
            Product.objects.filter(pk=product.pk).update(code="BYPASS")
        with self.assertRaisesMessage(ValueError, "bulk_update"):
            Product.objects.bulk_update([product], ["code"])
        with self.assertRaisesMessage(ValueError, "bulk_create"):
            Product.objects.bulk_create([])

    def test_resolver_uses_registry_for_all_identifier_fields(self):
        product = self.create_product(
            "SCAN-CODE",
            gtin="6901234567892",
            unit_barcode="UNIT-SCAN",
            external_code="EXTERNAL-SCAN",
        )
        carton_package = ProductPackage.objects.create(
            product=product,
            uom=self.carton_uom,
            qty_in_base=24,
        )
        product.carton_barcode = "CARTON-SCAN"
        product.carton_package = carton_package
        product.full_clean()
        product.save(update_fields=["carton_barcode", "carton_package"])

        for value in (
            product.code,
            product.sku,
            product.gtin,
            product.unit_barcode,
            product.carton_barcode,
            product.external_code,
        ):
            with self.subTest(value=value):
                result = default_resolver(self.owner.pk, value.lower())
                self.assertEqual(result.product_id, product.pk)

        Product.all_objects.filter(pk=product.pk).update(is_deleted=True)
        self.assertIsNone(default_resolver(self.owner.pk, product.code).product_id)


class ProductIdentifierConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.uom = ProductUom.objects.create(code="RID-CON-EA", name="件")
        self.owner = Owner.objects.create(code="RIDCON", name="并发注册表货主")

    def test_concurrent_cross_field_claim_allows_only_one_product(self):
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def create_product(code, field):
            connections.close_all()
            barrier.wait()
            try:
                Product.objects.create(
                    owner_id=self.owner.pk,
                    code=code,
                    name=code,
                    base_uom_id=self.uom.pk,
                    expiry_control=False,
                    expiry_basis=None,
                    **{field: "CONCURRENT-SHARED"},
                )
            except IntegrityError:
                outcome = "conflict"
            else:
                outcome = "created"
            finally:
                connections.close_all()
            with outcome_lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(
                target=create_product,
                args=("CONCURRENT-A", "unit_barcode"),
            ),
            threading.Thread(
                target=create_product,
                args=("CONCURRENT-B", "external_code"),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["created", "conflict"])
        self.assertEqual(
            ProductIdentifierRegistry.objects.filter(
                owner=self.owner,
                normalized_value="CONCURRENT-SHARED",
            ).count(),
            1,
        )
