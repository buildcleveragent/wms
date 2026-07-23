import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.products.management.commands.seed_test_product_categories import (
    CATEGORY_SPECS,
    Command,
)
from allapp.products.models import Product, ProductCategory, ProductUom


class SeedTestProductCategoriesTests(TestCase):
    def setUp(self):
        self.database_patch = patch.object(
            Command, "_validate_database", return_value="wms_db"
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)

        self.owner = Owner.objects.create(code="SEED-CAT", name="测试分类货主")
        self.uom = ProductUom.objects.create(
            code="SEED-CAT-EA",
            name="测试件",
            kind="COUNT",
            decimal_places=0,
        )
        self.manual_category = ProductCategory.objects.create(
            code="MANUAL-CAT",
            name="人工分类",
        )
        self.manual_product = self._product(
            "MANUAL", "人工分类商品", category=self.manual_category
        )
        self.soy_product = self._product("SOY", "原味全豆豆奶250ml")
        self.egg_product = self._product("EGG", "国曼10号鸡蛋")
        self.pack_product = self._product("PACK", "坚果包装封口铝膜")
        self.unknown_product = self._product("UNKNOWN", "无法识别的测试货物")

    def _product(self, code, name, category=None):
        return Product.objects.create(
            owner=self.owner,
            code=code,
            name=name,
            base_uom=self.uom,
            category=category,
            expiry_control=False,
        )

    def _run(self, *args):
        output = io.StringIO()
        call_command("seed_test_product_categories", *args, stdout=output)
        return output.getvalue()

    def test_default_mode_only_previews_without_database_writes(self):
        category_count = ProductCategory.objects.count()

        output = self._run()

        self.assertIn("数据库未修改", output)
        self.assertEqual(ProductCategory.objects.count(), category_count)
        self.soy_product.refresh_from_db()
        self.assertIsNone(self.soy_product.category_id)

    def test_apply_builds_three_levels_and_only_classifies_blank_products(self):
        original = {
            product.id: (
                product.name,
                product.spec,
                product.price,
                product.updated_at,
            )
            for product in Product.objects.all()
        }

        output = self._run("--apply")

        self.assertIn("执行完成", output)
        self.manual_product.refresh_from_db()
        self.assertEqual(self.manual_product.category_id, self.manual_category.id)

        expected_paths = {
            self.soy_product.id: "饮料 > 乳豆饮品 > 豆奶",
            self.egg_product.id: "生鲜 > 蛋品乳品 > 鸡蛋",
            self.pack_product.id: "包装 > 食品包装 > 封口膜",
            self.unknown_product.id: "其他商品 > 待分类 > 综合商品",
        }
        for product_id, expected_path in expected_paths.items():
            product = Product.objects.select_related("category__parent__parent").get(
                pk=product_id
            )
            self.assertEqual(product.category.full_path, expected_path)

        self.assertFalse(Product.objects.filter(category__isnull=True).exists())
        for category in ProductCategory.objects.select_related(
            "parent", "parent__parent"
        ):
            self.assertLessEqual(category.depth, 3)
            self.assertTrue(category.has_active_path())

        for product in Product.objects.all():
            self.assertEqual(
                (
                    product.name,
                    product.spec,
                    product.price,
                    product.updated_at,
                ),
                original[product.id],
            )

    def test_apply_is_idempotent(self):
        self._run("--apply")
        category_count = ProductCategory.objects.count()
        product_categories = dict(Product.objects.values_list("id", "category_id"))

        output = self._run("--apply")

        self.assertIn("新增分类 0 个，归类商品 0 个", output)
        self.assertEqual(ProductCategory.objects.count(), category_count)
        self.assertEqual(
            dict(Product.objects.values_list("id", "category_id")),
            product_categories,
        )
        self.assertEqual(
            ProductCategory.objects.filter(
                code__in=[code for code, _name, _parent, _sort in CATEGORY_SPECS]
            ).count(),
            len(CATEGORY_SPECS),
        )

    def test_failure_after_category_creation_rolls_back_everything(self):
        category_count = ProductCategory.objects.count()
        with patch.object(
            Command,
            "_assign_products",
            side_effect=CommandError("模拟归类失败"),
        ):
            with self.assertRaisesMessage(CommandError, "模拟归类失败"):
                self._run("--apply")

        self.assertEqual(ProductCategory.objects.count(), category_count)
        self.soy_product.refresh_from_db()
        self.assertIsNone(self.soy_product.category_id)
