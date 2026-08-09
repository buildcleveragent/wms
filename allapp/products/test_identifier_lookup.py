from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner

from .identifier_lookup import (
    exact_matching_product_ids,
    filter_by_product_search,
    matching_product_ids,
)
from .identifier_services import (
    add_external_identifier,
    add_product_barcode,
    set_identifier_active,
)
from .models import Product, ProductBarcode, ProductPackage, ProductUom


class UnifiedProductIdentifierLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="LOOKUP", name="查询货主")
        cls.other_owner = Owner.objects.create(code="LOOKUP2", name="其他货主")
        cls.each = ProductUom.objects.create(code="LOOKUP-EA", name="瓶")
        cls.carton = ProductUom.objects.create(code="LOOKUP-CTN", name="箱")

    def make_product(self, code, *, owner=None, name=None, spec=None):
        return Product.objects.create(
            owner=owner or self.owner,
            code=code,
            name=name or code,
            spec=spec,
            base_uom=self.each,
            expiry_control=False,
            expiry_basis=None,
        )

    def ids(self, term):
        return set(matching_product_ids(term).values_list("pk", flat=True))

    def test_searches_full_product_and_all_effective_identifier_sources(self):
        product = self.make_product(
            "OWNER-CODE-ABC", name="青柠气泡水", spec="330ml 罐装"
        )
        package = ProductPackage.objects.create(
            product=product, uom=self.carton, qty_in_base=24
        )
        values = [
            add_product_barcode(
                product=product,
                barcode="6901234567892",
                barcode_type=ProductBarcode.BarcodeType.GTIN,
            ).barcode,
            add_product_barcode(
                product=product,
                barcode="UNIT-LOOKUP-1",
                barcode_type=ProductBarcode.BarcodeType.UNIT,
            ).barcode,
            add_product_barcode(
                product=product,
                barcode="CARTON-LOOKUP-1",
                barcode_type=ProductBarcode.BarcodeType.CARTON,
                package=package,
                project=False,
            ).barcode,
            add_product_barcode(
                product=product,
                barcode="PACKAGE-LOOKUP-1",
                barcode_type=ProductBarcode.BarcodeType.PACKAGE,
                package=package,
                project=False,
            ).barcode,
            add_product_barcode(
                product=product,
                barcode="OTHER-LOOKUP-1",
                barcode_type=ProductBarcode.BarcodeType.OTHER,
            ).barcode,
            add_external_identifier(
                product=product,
                source_system="OMS",
                external_code="OMS-LOOKUP-1",
            ).external_code,
        ]

        for term in (
            "青柠气泡",
            "330ML",
            "owner-code",
            product.sku.lower(),
            *(value.lower()[2:-1] for value in values),
        ):
            with self.subTest(term=term):
                self.assertIn(product.pk, self.ids(f"  {term}  "))

    def test_inactive_future_expired_and_deleted_history_is_not_searchable(self):
        product = self.make_product("LIFECYCLE")
        now = timezone.now()
        retired = add_product_barcode(
            product=product, barcode="RETIRED-LOOKUP", barcode_type="OTHER"
        )
        set_identifier_active(retired, False)
        add_product_barcode(
            product=product,
            barcode="FUTURE-LOOKUP",
            barcode_type="OTHER",
            valid_from=now + timedelta(days=1),
        )
        add_product_barcode(
            product=product,
            barcode="EXPIRED-LOOKUP",
            barcode_type="OTHER",
            valid_to=now - timedelta(days=1),
        )
        deleted = add_external_identifier(
            product=product,
            source_system="ERP",
            external_code="DELETED-LOOKUP",
        )
        type(deleted).all_objects.filter(pk=deleted.pk).update(is_deleted=True)

        for term in (
            "RETIRED-LOOKUP",
            "FUTURE-LOOKUP",
            "EXPIRED-LOOKUP",
            "DELETED-LOOKUP",
        ):
            with self.subTest(term=term):
                self.assertNotIn(product.pk, self.ids(term))

    def test_related_filter_keeps_outer_owner_scope_and_does_not_duplicate(self):
        own = self.make_product("SCOPED-OWN")
        other = self.make_product("SCOPED-OTHER", owner=self.other_owner)
        add_product_barcode(
            product=own, barcode="SHARED-PART-OWN", barcode_type="OTHER"
        )
        add_product_barcode(
            product=other, barcode="SHARED-PART-OTHER", barcode_type="OTHER"
        )

        scoped = filter_by_product_search(
            Product.objects.filter(owner=self.owner),
            "shared-part",
            product_field="pk",
        )
        self.assertEqual(list(scoped), [own])

    def test_exact_lookup_uses_only_current_sources_and_allows_cross_owner_results(
        self,
    ):
        first = self.make_product("EXACT-FIRST")
        second = self.make_product("EXACT-SECOND", owner=self.other_owner)
        for product in (first, second):
            add_product_barcode(
                product=product, barcode="CROSS-OWNER-EXACT", barcode_type="OTHER"
            )
        retired = add_product_barcode(
            product=first, barcode="EXACT-RETIRED", barcode_type="OTHER"
        )
        set_identifier_active(retired, False)

        self.assertEqual(
            set(
                exact_matching_product_ids(" cross-owner-exact ").values_list(
                    "product_id", flat=True
                )
            ),
            {first.pk, second.pk},
        )
        self.assertFalse(exact_matching_product_ids("EXACT-RETIRED").exists())

    def test_inactive_or_deleted_package_disables_package_semantic_barcode(self):
        product = self.make_product("PACKAGE-LIFECYCLE")
        package = ProductPackage.objects.create(
            product=product, uom=self.carton, qty_in_base=12
        )
        record = add_product_barcode(
            product=product,
            barcode="PACKAGE-LIFECYCLE-CODE",
            barcode_type=ProductBarcode.BarcodeType.PACKAGE,
            package=package,
        )

        package.is_active = False
        package.save(update_fields=["is_active"])
        self.assertNotIn(product.pk, self.ids(record.barcode))
        self.assertFalse(exact_matching_product_ids(record.barcode).exists())

        package.is_active = True
        package.is_deleted = True
        package.save(update_fields=["is_active", "is_deleted"])
        self.assertNotIn(product.pk, self.ids(record.barcode))
        self.assertFalse(exact_matching_product_ids(record.barcode).exists())
