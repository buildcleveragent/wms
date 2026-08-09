from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.tasking.plugins.barcodes import default_resolver

from .identifier_services import add_product_barcode
from .models import Product, ProductBarcode, ProductPackage, ProductUom


class ProductPackageBarcodeHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="PKGH", name="包装历史货主")
        cls.base_uom = ProductUom.objects.create(code="PKGH-EA", name="瓶")
        cls.carton_uom = ProductUom.objects.create(code="PKGH-CTN", name="箱")

    def product(self, code="WATER"):
        return Product.objects.create(
            owner=self.owner, code=code, name=code, base_uom=self.base_uom,
            expiry_control=False, expiry_basis=None,
        )

    def package(self, product, qty=24, barcode=None):
        return ProductPackage.objects.create(
            product=product, uom=self.carton_uom, qty_in_base=qty, barcode=barcode,
        )

    def test_initial_package_barcode_is_history_record_and_resolves_snapshot(self):
        product = self.product()
        package = self.package(product, barcode="PKG-24")
        record = ProductBarcode.objects.get(product=product, barcode_type="PACKAGE")
        self.assertEqual(record.package_id, package.pk)
        self.assertEqual(record.qty_in_base, 24)
        resolved = default_resolver(self.owner.pk, "pkg-24")
        self.assertEqual(resolved.code_type, "PACKAGE")
        self.assertEqual(resolved.product_package_id, package.pk)
        self.assertEqual(resolved.pack_qty, 24)

    def test_carton_history_keeps_old_conversion_after_new_box_spec(self):
        product = self.product()
        old_package = self.package(product, 24)
        old = add_product_barcode(
            product=product, barcode="BOX-OLD", barcode_type="CARTON",
            package=old_package, is_primary=True,
        )
        new_uom = ProductUom.objects.create(code="PKGH-CTN2", name="新箱")
        new_package = ProductPackage.objects.create(product=product, uom=new_uom, qty_in_base=30)
        new = add_product_barcode(
            product=product, barcode="BOX-NEW", barcode_type="CARTON",
            package=new_package, is_primary=True,
        )
        self.assertEqual(default_resolver(self.owner.pk, old.barcode).pack_qty, 24)
        self.assertEqual(default_resolver(self.owner.pk, new.barcode).pack_qty, 30)
        self.assertEqual(default_resolver(self.owner.pk, "BOX-OLD*12").pack_qty, 12)

    def test_same_value_with_different_packaging_semantics_is_rejected(self):
        product = self.product()
        package = self.package(product)
        add_product_barcode(product=product, barcode="SEMANTIC", barcode_type="CARTON", package=package)
        with self.assertRaisesRegex(ValidationError, "包装|换算语义"):
            add_product_barcode(product=product, barcode="SEMANTIC", barcode_type="UNIT")

    def test_package_barcode_projection_can_only_change_by_new_primary(self):
        product = self.product()
        package = self.package(product, barcode="PKG-A")
        package.barcode = "PKG-B"
        with self.assertRaises(ValidationError):
            package.save(update_fields=["barcode"])
        new = add_product_barcode(
            product=product, barcode="PKG-B", barcode_type="PACKAGE",
            package=package, is_primary=True,
        )
        package.refresh_from_db()
        self.assertEqual(package.barcode, new.barcode)
        self.assertEqual(default_resolver(self.owner.pk, "PKG-A").product_id, product.pk)

    def test_identity_fields_cannot_be_changed_after_creation(self):
        product = self.product()
        record = add_product_barcode(product=product, barcode="IMMUTABLE", barcode_type="OTHER")
        record.barcode = "CHANGED"
        record._identifier_service_write = True
        with self.assertRaises(ValidationError):
            record.save()
