from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from allapp.products.management.commands.seed_test_product_categories import (
    CATEGORY_SPECS,
    FALLBACK_CATEGORY_CODE,
    Command,
    classify_product_name,
)


class SeedTestProductCategoriesUnitTests(SimpleTestCase):
    def test_category_specs_are_unique_parent_first_and_at_most_three_levels(self):
        parent_by_code = {}
        sibling_names = set()
        for code, name, parent_code, _sort_order in CATEGORY_SPECS:
            self.assertNotIn(code, parent_by_code)
            if parent_code:
                self.assertIn(parent_code, parent_by_code)
            sibling_key = (parent_code, name)
            self.assertNotIn(sibling_key, sibling_names)
            sibling_names.add(sibling_key)
            parent_by_code[code] = parent_code

            depth = 1
            ancestor_code = parent_code
            while ancestor_code:
                depth += 1
                ancestor_code = parent_by_code[ancestor_code]
            self.assertLessEqual(depth, 3)

    def test_specific_product_keywords_map_to_expected_leaf_categories(self):
        cases = {
            "原味全豆豆奶250ml": "TST-02-DAIRY-SOY",
            "国曼10号鸡蛋": "TST-06-EGG-CHICKEN",
            "坚果包装封口铝膜": "TST-09-FOOD-SEAL",
            "浅香百合氨基酸沐浴露": "TST-07-PERSONAL-BATH",
            "AEX薰衣草洗衣液": "TST-07-HOME-LAUNDRY",
            "黄葡萄干350g": "TST-04-DRIED-RAISIN",
        }
        for product_name, expected_code in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    classify_product_name(product_name),
                    (expected_code, True),
                )

    def test_gift_box_product_is_not_mistaken_for_packaging_material(self):
        self.assertEqual(
            classify_product_name("红装大礼盒250ml*12"),
            (FALLBACK_CATEGORY_CODE, False),
        )

    def test_unknown_product_uses_general_fallback(self):
        self.assertEqual(
            classify_product_name("无法识别的测试货物"),
            (FALLBACK_CATEGORY_CODE, False),
        )

    def test_database_guard_rejects_other_database_names(self):
        command = Command()
        with patch(
            "allapp.products.management.commands.seed_test_product_categories."
            "connection.settings_dict",
            {"NAME": "other_db"},
        ):
            with self.assertRaisesMessage(CommandError, "只允许写入 wms_db"):
                command._validate_database()
