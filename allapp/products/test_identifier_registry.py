import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.tasking.plugins.barcodes import default_resolver

from .identifier_services import (
    add_external_identifier,
    add_product_barcode,
    set_barcode_primary,
    set_external_primary,
    set_identifier_active,
)
from .models import (
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductIdentifierRegistry,
    ProductUom,
)


class ProductIdentifierHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="HIST", name="历史标识货主")
        cls.other_owner = Owner.objects.create(code="HIST2", name="其他货主")
        cls.uom = ProductUom.objects.create(code="HIST-EA", name="件")

    def product(self, code, owner=None, **identifiers):
        return Product.objects.create(
            owner=owner or self.owner,
            code=code,
            name=code,
            base_uom=self.uom,
            expiry_control=False,
            expiry_basis=None,
            **identifiers,
        )

    def test_initial_legacy_values_backfill_history_and_registry(self):
        product = self.product(
            "INIT",
            gtin="6901234567892",
            unit_barcode="UNIT-OLD",
            external_code="ERP-OLD",
        )
        self.assertTrue(
            ProductBarcode.objects.filter(
                product=product, barcode_type="GTIN", is_primary=True
            ).exists()
        )
        self.assertTrue(
            ProductExternalIdentifier.objects.filter(
                product=product, source_system="LEGACY", is_primary=True
            ).exists()
        )
        self.assertEqual(
            set(
                ProductIdentifierRegistry.objects.filter(product=product).values_list(
                    "normalized_value", flat=True
                )
            ),
            {product.code, product.sku, "6901234567892", "UNIT-OLD", "ERP-OLD"},
        )

    def test_stable_fields_and_projection_direct_save_are_rejected(self):
        product = self.product("LOCKED", gtin="6901234567892")
        for field, value in (
            ("code", "NEW-CODE"),
            ("sku", "NEW-SKU"),
            ("gtin", "6901234567885"),
        ):
            current = Product.objects.get(pk=product.pk)
            setattr(current, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                current.save(update_fields=[field])

    def test_new_primary_appends_and_old_gtin_remains_scannable(self):
        product = self.product("CHANGE", gtin="6901234567892")
        old = ProductBarcode.objects.get(product=product, barcode_type="GTIN")
        new = add_product_barcode(
            product=product,
            barcode="6901234567885",
            barcode_type="GTIN",
            is_primary=True,
        )
        old.refresh_from_db()
        product.refresh_from_db()
        self.assertFalse(old.is_primary)
        self.assertTrue(new.is_primary)
        self.assertEqual(product.gtin, new.barcode)
        self.assertEqual(
            default_resolver(self.owner.pk, old.barcode).product_id, product.pk
        )
        self.assertEqual(
            default_resolver(self.owner.pk, new.barcode).product_id, product.pk
        )

    def test_retired_identifier_is_reserved_and_reports_inactive(self):
        product = self.product("RETIRE")
        record = add_product_barcode(
            product=product, barcode="OLD-CODE", barcode_type="OTHER"
        )
        set_identifier_active(record, False)
        with self.assertRaisesRegex(ValidationError, "停用|过期"):
            default_resolver(self.owner.pk, "old-code")
        other = self.product("RETIRE-OTHER")
        with self.assertRaises(ValidationError):
            add_product_barcode(product=other, barcode="old-code", barcode_type="OTHER")

    def test_external_sources_share_value_only_with_same_product(self):
        product = self.product("EXT")
        add_external_identifier(
            product=product,
            source_system="ERP",
            external_code="PARTNER-1",
            is_primary=True,
        )
        add_external_identifier(
            product=product,
            source_system="OMS",
            external_code="PARTNER-1",
            is_primary=True,
        )
        self.assertEqual(
            default_resolver(self.owner.pk, "partner-1").code_type, "EXTERNAL"
        )
        with self.assertRaises(ValidationError):
            add_external_identifier(
                product=self.product("EXT2"),
                source_system="ERP",
                external_code="PARTNER-1",
            )

    def test_cross_owner_reuse_is_allowed(self):
        first = self.product("COMMON")
        second = self.product("COMMON", owner=self.other_owner)
        add_product_barcode(product=first, barcode="SHARED-OWNER", barcode_type="OTHER")
        add_product_barcode(
            product=second, barcode="SHARED-OWNER", barcode_type="OTHER"
        )

    def test_hard_delete_and_bulk_identity_writes_are_guarded(self):
        product = self.product("GUARD")
        with self.assertRaises(ValidationError):
            product.delete()
        with self.assertRaises(ValueError):
            Product.objects.filter(pk=product.pk).update(code="BYPASS")
        record = add_external_identifier(
            product=product, source_system="ERP", external_code="X"
        )
        with self.assertRaises(ValidationError):
            record.delete()
        with self.assertRaises(ValueError):
            ProductExternalIdentifier.objects.filter(pk=record.pk).update(
                external_code="Y"
            )

    def test_ineffective_barcode_cannot_become_primary_or_demote_current_primary(self):
        product = self.product("PRIMARY-BARCODE")
        current = add_product_barcode(
            product=product,
            barcode="6901234567892",
            barcode_type=ProductBarcode.BarcodeType.GTIN,
            is_primary=True,
        )
        future = add_product_barcode(
            product=product,
            barcode="6901234567885",
            barcode_type=ProductBarcode.BarcodeType.GTIN,
            valid_from=timezone.now() + datetime.timedelta(days=1),
        )

        with self.assertRaisesRegex(ValidationError, "不能设为主标识"):
            set_barcode_primary(future)

        current.refresh_from_db()
        self.assertTrue(current.is_primary)

    def test_ineffective_external_identifier_cannot_become_primary(self):
        product = self.product("PRIMARY-EXTERNAL")
        current = add_external_identifier(
            product=product,
            source_system="ERP",
            external_code="ERP-CURRENT",
            is_primary=True,
        )
        retired = add_external_identifier(
            product=product,
            source_system="ERP",
            external_code="ERP-RETIRED",
        )
        set_identifier_active(retired, False)

        with self.assertRaisesRegex(ValidationError, "不能设为主标识"):
            set_external_primary(retired)

        current.refresh_from_db()
        self.assertTrue(current.is_primary)
