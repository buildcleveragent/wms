from pathlib import Path

from django.test import SimpleTestCase


CLIENT_ROOT = Path(__file__).resolve().parents[2] / "wmsownersale"


class OwnerPriceClientContractTests(SimpleTestCase):
    def test_shared_formula_uses_discount_percentage(self):
        source = (CLIENT_ROOT / "utils" / "pricing.js").read_text(encoding="utf-8")

        self.assertIn("original * (100 - discount) / 100", source)
        self.assertNotIn("max_discount *", source)

    def test_registered_price_pages_use_shared_guard(self):
        for relative_path in ("pages/products/search.vue", "pages/orders/cart.vue"):
            with self.subTest(path=relative_path):
                source = (CLIENT_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("@/utils/pricing", source)
                self.assertNotIn("max_discount*it.orig_price", source)
                self.assertNotIn("max_discount * it.orig_price", source)

    def test_cart_preserves_original_price_and_nullable_discount(self):
        source = (CLIENT_ROOT / "store" / "cart.js").read_text(encoding="utf-8")

        self.assertIn("orig_price: Number(p.orig_price ?? p.price ?? 0)", source)
        self.assertIn("max_discount: p.max_discount == null ? null", source)
        self.assertIn("product_min_price: p.product_min_price == null ? null", source)

    def test_search_initializes_guard_before_price_can_be_edited(self):
        source = (CLIENT_ROOT / "pages" / "products" / "search.vue").read_text(
            encoding="utf-8"
        )

        self.assertIn("normalized.results.forEach(initializePriceGuard)", source)
        self.assertIn("orig_price: Number(p.orig_price ?? p.price ?? 0)", source)

    def test_cart_rechecks_every_item_before_submit(self):
        source = (CLIENT_ROOT / "pages" / "orders" / "cart.vue").read_text(
            encoding="utf-8"
        )

        self.assertIn("cart.items.find(it => !isPriceAllowed(it))", source)

    def test_unregistered_legacy_cart_has_been_removed(self):
        self.assertFalse((CLIENT_ROOT / "pages" / "products" / "cart.vue").exists())
