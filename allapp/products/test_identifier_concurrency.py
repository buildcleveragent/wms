import threading

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TransactionTestCase

from allapp.baseinfo.models import Owner

from .identifier_services import (
    IdentifierConcurrencyError,
    add_product_barcode,
    set_barcode_primary,
)
from .models import Product, ProductBarcode, ProductUom


class ProductIdentifierConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="ID-CON", name="标识并发货主")
        self.uom = ProductUom.objects.create(code="ID-CON-EA", name="件")
        self.first = Product.objects.create(
            owner=self.owner,
            code="ID-CON-P1",
            name="并发商品一",
            base_uom=self.uom,
        )
        self.second = Product.objects.create(
            owner=self.owner,
            code="ID-CON-P2",
            name="并发商品二",
            base_uom=self.uom,
        )

    def test_concurrent_products_cannot_claim_the_same_identifier(self):
        barrier = threading.Barrier(2)
        results = []
        result_lock = threading.Lock()

        def claim(product_id):
            close_old_connections()
            try:
                product = Product.objects.get(pk=product_id)
                barrier.wait(timeout=10)
                record = add_product_barcode(
                    product=product,
                    barcode="ID-CON-SHARED",
                    barcode_type=ProductBarcode.BarcodeType.OTHER,
                )
                outcome = ("created", record.product_id)
            except (IdentifierConcurrencyError, ValidationError) as exc:
                outcome = ("rejected", type(exc).__name__)
            finally:
                close_old_connections()
            with result_lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=claim, args=(self.first.pk,)),
            threading.Thread(target=claim, args=(self.second.pk,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([result[0] for result in results].count("created"), 1)
        self.assertEqual([result[0] for result in results].count("rejected"), 1)
        self.assertEqual(
            ProductBarcode.objects.filter(normalized_value="ID-CON-SHARED").count(),
            1,
        )

    def test_concurrent_primary_switches_leave_exactly_one_primary(self):
        records = [
            add_product_barcode(
                product=self.first,
                barcode=f"ID-CON-PRIMARY-{number}",
                barcode_type=ProductBarcode.BarcodeType.OTHER,
            )
            for number in (1, 2)
        ]
        barrier = threading.Barrier(2)
        errors = []

        def switch(record_id):
            close_old_connections()
            try:
                record = ProductBarcode.objects.get(pk=record_id)
                barrier.wait(timeout=10)
                set_barcode_primary(record)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=switch, args=(record.pk,)) for record in records]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(
            ProductBarcode.objects.filter(
                product=self.first,
                barcode_type=ProductBarcode.BarcodeType.OTHER,
                is_primary=True,
            ).count(),
            1,
        )
