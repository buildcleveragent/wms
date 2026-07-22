import importlib

from django.apps import apps
from django.db import IntegrityError
from django.test import TestCase

from allapp.baseinfo.models import Owner

from .models import Product, ProductUom


class ProductSkuSequenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom = ProductUom.objects.create(code="SEQ-EA", name="件")

    def create_product(self, owner, code, **kwargs):
        return Product.objects.create(
            owner=owner,
            code=code,
            name=code,
            base_uom=self.uom,
            expiry_control=False,
            expiry_basis=None,
            **kwargs,
        )

    def test_new_product_uses_current_sequence_and_increments_owner(self):
        owner = Owner.objects.create(
            code="jqrt",
            name="序号货主",
            next_sku_sequence=100,
        )

        product = self.create_product(owner, "PRODUCT-100", sku="CALLER-SUPPLIED")

        owner.refresh_from_db()
        self.assertEqual(product.sku, "jqrt-100")
        self.assertEqual(owner.next_sku_sequence, 101)

    def test_soft_deleted_product_does_not_release_sequence(self):
        owner = Owner.objects.create(code="DEL", name="软删除序号货主")
        first = self.create_product(owner, "PRODUCT-1")
        Product.all_objects.filter(pk=first.pk).update(is_deleted=True)

        second = self.create_product(owner, "PRODUCT-2")

        owner.refresh_from_db()
        self.assertEqual(first.sku, "DEL-1")
        self.assertEqual(second.sku, "DEL-2")
        self.assertEqual(owner.next_sku_sequence, 3)

    def test_migration_initializes_from_active_and_soft_deleted_products(self):
        owner = Owner.objects.create(code="MIG", name="迁移序号货主")
        first = self.create_product(owner, "PRODUCT-1")
        self.create_product(owner, "PRODUCT-2")
        Product.all_objects.filter(pk=first.pk).update(is_deleted=True)
        Owner.all_objects.filter(pk=owner.pk).update(next_sku_sequence=1)

        migration = importlib.import_module(
            "allapp.baseinfo.migrations.0004_owner_next_sku_sequence"
        )
        migration.initialize_next_sku_sequence(apps, None)

        owner.refresh_from_db()
        self.assertEqual(owner.next_sku_sequence, 3)

    def test_updating_product_does_not_consume_another_sequence(self):
        owner = Owner.objects.create(code="UPD", name="更新序号货主")
        product = self.create_product(owner, "PRODUCT-1")

        product.name = "更新后的名称"
        product.save()

        owner.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(product.sku, "UPD-1")
        self.assertEqual(owner.next_sku_sequence, 2)

    def test_failed_insert_does_not_consume_sequence(self):
        owner = Owner.objects.create(code="FAIL", name="失败序号货主")
        self.create_product(owner, "DUPLICATE")

        with self.assertRaises(IntegrityError):
            self.create_product(owner, "DUPLICATE")

        owner.refresh_from_db()
        self.assertEqual(owner.next_sku_sequence, 2)
