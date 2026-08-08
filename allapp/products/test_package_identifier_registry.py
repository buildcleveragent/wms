import importlib
import threading

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Owner
from allapp.tasking.plugins.barcodes import default_resolver

from .models import (
    Product,
    ProductIdentifierRegistry,
    ProductPackage,
    ProductUom,
)


class ProductPackageIdentifierRegistryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="PKGRID", name="包装注册表货主")
        cls.other_owner = Owner.objects.create(
            code="PKGOTHER", name="其他包装注册表货主"
        )
        cls.base_uom = ProductUom.objects.create(code="PKG-EA", name="件")
        cls.carton_uom = ProductUom.objects.create(code="PKG-CTN", name="箱")

    def create_product(self, code, *, owner=None, **identifiers):
        return Product.objects.create(
            owner=owner or self.owner,
            code=code,
            name=code,
            base_uom=self.base_uom,
            expiry_control=False,
            expiry_basis=None,
            **identifiers,
        )

    def create_package(self, product, barcode, *, uom=None, **kwargs):
        return ProductPackage.objects.create(
            product=product,
            uom=uom or self.carton_uom,
            qty_in_base=12,
            barcode=barcode,
            **kwargs,
        )

    def bind_carton(self, product, barcode):
        package = ProductPackage.objects.create(
            product=product,
            uom=self.carton_uom,
            qty_in_base=12,
        )
        product.carton_barcode = barcode
        product.carton_package = package
        product.full_clean()
        product.save(update_fields=["carton_barcode", "carton_package"])
        return package

    def test_package_barcode_registers_normalized_owner_identifier(self):
        product = self.create_product("PKG-ITEM")
        package = self.create_package(product, "  pkg-bar  ")

        registry = ProductIdentifierRegistry.objects.get(
            product_package=package
        )
        self.assertEqual(registry.owner_id, self.owner.pk)
        self.assertEqual(registry.product_id, product.pk)
        self.assertEqual(registry.normalized_value, "PKG-BAR")
        resolved = default_resolver(self.owner.pk, "pkg-bar")
        self.assertEqual(resolved.code_type, "PACKAGE")
        self.assertEqual(resolved.product_package_id, package.pk)
        self.assertEqual(resolved.uom_code, self.carton_uom.code)
        self.assertEqual(resolved.uom_name, self.carton_uom.name)
        self.assertEqual(resolved.pack_qty, 12)
        self.assertEqual(resolved.matched_fields, ["product_package.barcode"])

    def test_carton_binding_resolves_package_and_explicit_multiplier_overrides(self):
        product = self.create_product("PKG-CARTON-RESOLVE")
        package = self.bind_carton(product, "BOX-24")

        resolved = default_resolver(self.owner.pk, "box-24")
        self.assertEqual(resolved.product_id, product.pk)
        self.assertEqual(resolved.product_package_id, package.pk)
        self.assertEqual(resolved.code_type, "CARTON")
        self.assertEqual(resolved.matched_fields, ["carton_barcode"])
        self.assertEqual(resolved.pack_qty, 12)

        overridden = default_resolver(self.owner.pk, "BOX-24*30")
        self.assertEqual(overridden.pack_qty, 30)

    def test_non_numeric_identifier_type_comes_from_matched_field(self):
        product = self.create_product("NOT-A-SKU", external_code="PARTNER-REF")

        code_result = default_resolver(self.owner.pk, product.code)
        external_result = default_resolver(self.owner.pk, "partner-ref")

        self.assertEqual(code_result.code_type, "PRODUCT_CODE")
        self.assertEqual(code_result.matched_field, "code")
        self.assertEqual(external_result.code_type, "EXTERNAL")
        self.assertEqual(external_result.matched_field, "external_code")

    def test_carton_and_base_identifier_overlap_is_reported_as_conflict(self):
        product = self.create_product("PKG-SEMANTIC", unit_barcode="SAME-CODE")
        self.bind_carton(product, "SAME-CODE")

        with self.assertRaisesRegex(ValidationError, "编码冲突"):
            default_resolver(self.owner.pk, "same-code")

    def test_bound_carton_package_cannot_be_changed_or_disabled(self):
        product = self.create_product("PKG-BOUND")
        package = self.bind_carton(product, "BOUND-CARTON")

        package.is_active = False
        with self.assertRaisesRegex(ValidationError, "已绑定商品箱码"):
            package.save(update_fields=["is_active"])

        product.carton_barcode = "OTHER-CARTON"
        with self.assertRaisesRegex(ValidationError, "绑定后不可修改"):
            product.save(update_fields=["carton_barcode"])

    def test_package_barcode_conflicts_with_every_product_identifier(self):
        identifiers = {
            "code": "PKG-CODE",
            "sku": None,
            "gtin": "6901234567892",
            "unit_barcode": "PKG-UNIT",
            "carton_barcode": "PKG-CARTON",
            "external_code": "PKG-EXTERNAL",
        }
        product = self.create_product(
            identifiers["code"],
            gtin=identifiers["gtin"],
            unit_barcode=identifiers["unit_barcode"],
            external_code=identifiers["external_code"],
        )
        self.bind_carton(product, identifiers["carton_barcode"])
        identifiers["sku"] = product.sku

        for index, value in enumerate(identifiers.values(), start=1):
            with self.subTest(value=value):
                package = ProductPackage(
                    product=product,
                    uom=ProductUom.objects.create(
                        code=f"PKG-UOM-{index}", name=f"包装{index}"
                    ),
                    qty_in_base=index + 1,
                    barcode=f" {value.lower()} ",
                )
                with self.assertRaises(ValidationError) as raised:
                    package.full_clean()
                self.assertIn("barcode", raised.exception.message_dict)

    def test_package_barcode_conflicts_across_products_in_same_owner(self):
        first = self.create_product("PKG-FIRST")
        second = self.create_product("PKG-SECOND")
        self.create_package(first, "SHARED-PACKAGE")

        conflicting = ProductPackage(
            product=second,
            uom=self.carton_uom,
            qty_in_base=6,
            barcode=" shared-package ",
        )
        with self.assertRaises(ValidationError):
            conflicting.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            conflicting.save()

    def test_package_barcode_can_repeat_across_owners(self):
        first = self.create_product("PKG-OWNER-A")
        second = self.create_product("PKG-OWNER-B", owner=self.other_owner)

        self.create_package(first, "CROSS-OWNER")
        other = self.create_package(second, " cross-owner ")

        self.assertEqual(
            ProductIdentifierRegistry.objects.filter(
                normalized_value="CROSS-OWNER"
            ).count(),
            2,
        )
        self.assertEqual(other.product.owner_id, self.other_owner.pk)

    def test_update_clear_soft_delete_and_hard_delete_sync_registry(self):
        product = self.create_product("PKG-LIFECYCLE")
        package = self.create_package(product, "PKG-OLD")

        package.barcode = "PKG-NEW"
        package.save(update_fields=["barcode"])
        self.assertFalse(
            ProductIdentifierRegistry.objects.filter(
                normalized_value="PKG-OLD"
            ).exists()
        )
        self.assertTrue(
            ProductIdentifierRegistry.objects.filter(
                product_package=package,
                normalized_value="PKG-NEW",
            ).exists()
        )

        package.is_deleted = True
        package.save(update_fields=["is_deleted"])
        self.assertTrue(
            ProductIdentifierRegistry.objects.filter(
                product_package=package
            ).exists()
        )
        self.assertIsNone(
            default_resolver(self.owner.pk, "PKG-NEW").product_id
        )

        package.is_deleted = False
        package.is_active = False
        package.save(update_fields=["is_deleted", "is_active"])
        self.assertIsNone(
            default_resolver(self.owner.pk, "PKG-NEW").product_id
        )

        package.is_active = True
        package.save(update_fields=["is_active"])
        self.assertEqual(
            default_resolver(self.owner.pk, "PKG-NEW").product_id,
            product.pk,
        )

        package.barcode = None
        package.save(update_fields=["barcode"])
        self.assertFalse(
            ProductIdentifierRegistry.objects.filter(
                product_package=package
            ).exists()
        )

        package.barcode = "PKG-RELEASE"
        package.save(update_fields=["barcode"])
        package.delete()
        self.assertFalse(
            ProductIdentifierRegistry.objects.filter(
                normalized_value="PKG-RELEASE"
            ).exists()
        )

    def test_package_bulk_identifier_writes_are_guarded(self):
        product = self.create_product("PKG-GUARD")
        package = self.create_package(product, "PKG-GUARDED")

        with self.assertRaisesMessage(ValueError, "QuerySet.update"):
            ProductPackage.objects.filter(pk=package.pk).update(barcode="BYPASS")
        with self.assertRaisesMessage(ValueError, "bulk_update"):
            ProductPackage.objects.bulk_update([package], ["barcode"])
        with self.assertRaisesMessage(ValueError, "bulk_create"):
            ProductPackage.objects.bulk_create([])

    def test_data_migration_backfills_packages_and_detects_conflicts(self):
        product = self.create_product("PKG-MIGRATE")
        package = self.create_package(product, "PKG-BACKFILL")
        ProductIdentifierRegistry.objects.filter(
            product_package=package
        ).delete()
        migration = importlib.import_module(
            "allapp.products.migrations.0007_package_identifier_registry"
        )

        migration.backfill_package_identifiers(apps, None)

        self.assertTrue(
            ProductIdentifierRegistry.objects.filter(
                product_package=package,
                normalized_value="PKG-BACKFILL",
            ).exists()
        )

    def test_data_migration_rejects_package_product_field_conflict(self):
        product = self.create_product("PKG-MIGRATION-CONFLICT")
        package = ProductPackage(
            product=product,
            uom=self.carton_uom,
            qty_in_base=12,
            barcode=" pkg-migration-conflict ",
        )
        package.save_base(force_insert=True)
        migration = importlib.import_module(
            "allapp.products.migrations.0007_package_identifier_registry"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "包装条码注册表回填失败.*PKG-MIGRATION-CONFLICT",
        ):
            migration.backfill_package_identifiers(apps, None)


class ProductPackageIdentifierConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = Owner.objects.create(code="PKGCON", name="包装并发货主")
        self.base_uom = ProductUom.objects.create(code="PKGCON-EA", name="件")
        self.package_uoms = [
            ProductUom.objects.create(code=f"PKGCON-{index}", name=f"包装{index}")
            for index in range(2)
        ]
        self.products = [
            Product.objects.create(
                owner=self.owner,
                code=f"PKGCON-ITEM-{index}",
                name=f"并发商品{index}",
                base_uom=self.base_uom,
                expiry_control=False,
                expiry_basis=None,
            )
            for index in range(2)
        ]

    def test_concurrent_package_claim_allows_only_one(self):
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def create_package(index):
            connections.close_all()
            barrier.wait()
            try:
                ProductPackage.objects.create(
                    product_id=self.products[index].pk,
                    uom_id=self.package_uoms[index].pk,
                    qty_in_base=index + 2,
                    barcode="PKG-CONCURRENT-SHARED",
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
            threading.Thread(target=create_package, args=(index,))
            for index in range(2)
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
                normalized_value="PKG-CONCURRENT-SHARED",
            ).count(),
            1,
        )
