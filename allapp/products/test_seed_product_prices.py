import io
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.products.management.commands.seed_test_product_prices import Command
from allapp.products.models import Product, ProductCategory, ProductUom


class SeedTestProductPricesTests(TestCase):
    def setUp(self):
        self.database_patch = patch.object(
            Command, "_validate_database", return_value="wms_db"
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)

        self.owner = Owner.objects.create(code="SEED-PRICE", name="测试价格货主")
        self.uom = ProductUom.objects.create(
            code="SEED-PRICE-EA",
            name="测试件",
            kind="COUNT",
            decimal_places=0,
        )
        self.category = ProductCategory.objects.create(
            code="SEED-PRICE-CAT",
            name="测试价格分类",
        )
        self.blank_product = self._product("BLANK", "空价格商品")
        self.zero_product = self._product("ZERO", "零价格商品", price=Decimal("0"))
        self.priced_product = self._product(
            "PRICED",
            "已有价格商品",
            price=Decimal("12.34"),
        )

    def _product(self, code, name, price=None):
        return Product.objects.create(
            owner=self.owner,
            code=code,
            name=name,
            base_uom=self.uom,
            category=self.category,
            expiry_control=False,
            price=price,
        )

    def _run(self, *args):
        output = io.StringIO()
        call_command("seed_test_product_prices", *args, stdout=output)
        return output.getvalue()

    def test_default_mode_only_previews(self):
        output = self._run()

        self.assertIn("数据库未修改", output)
        self.blank_product.refresh_from_db()
        self.assertIsNone(self.blank_product.price)
        self.priced_product.refresh_from_db()
        self.assertEqual(self.priced_product.price, Decimal("12.34"))

    def test_apply_only_fills_missing_or_non_positive_prices(self):
        original = {
            product.id: (
                product.name,
                product.category_id,
                product.min_price,
                product.updated_at,
            )
            for product in Product.objects.all()
        }

        output = self._run("--apply")

        self.assertIn("补充价格 2 个", output)
        self.blank_product.refresh_from_db()
        self.zero_product.refresh_from_db()
        self.priced_product.refresh_from_db()
        self.assertGreater(self.blank_product.price, 0)
        self.assertGreater(self.zero_product.price, 0)
        self.assertEqual(self.priced_product.price, Decimal("12.34"))

        for product in Product.objects.all():
            self.assertEqual(
                (
                    product.name,
                    product.category_id,
                    product.min_price,
                    product.updated_at,
                ),
                original[product.id],
            )

    def test_apply_is_idempotent(self):
        self._run("--apply")
        prices = dict(Product.objects.values_list("id", "price"))

        output = self._run("--apply")

        self.assertIn("补充价格 0 个", output)
        self.assertEqual(dict(Product.objects.values_list("id", "price")), prices)

    def test_failure_rolls_back_partial_price_updates(self):
        def fail_after_update(_command, products):
            Product.objects.filter(pk=products[0].pk).update(price=Decimal("9.99"))
            raise CommandError("模拟价格更新失败")

        with patch.object(Command, "_apply_prices", new=fail_after_update):
            with self.assertRaisesMessage(CommandError, "模拟价格更新失败"):
                self._run("--apply")

        self.blank_product.refresh_from_db()
        self.assertIsNone(self.blank_product.price)
