from pathlib import Path

from django.test import SimpleTestCase


CLIENT_ROOT = Path(__file__).resolve().parents[2] / "wmsownersale"


class CashCustomerClientContractTests(SimpleTestCase):
    def read(self, relative_path):
        return (CLIENT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_customer_selection_passes_code_and_blocks_incomplete_data(self):
        source = self.read("pages/customers/select.vue")
        choose_block = source[source.index("function choose(c)") : source.index("onLoad(")]

        self.assertIn("code: c.code", choose_block)
        self.assertIn("const selected = cart.setCustomer", choose_block)
        self.assertIn("if (!selected)", choose_block)
        self.assertIn("客户数据不完整，请刷新重试", choose_block)
        self.assertLess(choose_block.index("if (!selected)"), choose_block.index("uni.redirectTo"))

    def test_cart_store_normalizes_and_preserves_customer_identity(self):
        source = self.read("store/cart.js")

        self.assertIn("normalizeSelectedCustomer", source)
        self.assertIn("if (!customer) return false", source)
        self.assertIn("this.customer = customer", source)
        self.assertIn("return true", source)

    def test_shared_cash_rule_uses_normalized_code_only(self):
        source = self.read("utils/customer.js")

        self.assertIn("trim().toUpperCase()", source)
        self.assertIn("normalizeCustomerCode(customer?.code) === 'CASH'", source)
        self.assertNotIn("customer?.name", source[source.index("export function isCashCustomer") :])
        self.assertNotIn("一件代发", source)

    def test_registered_cart_uses_shared_rule_and_keeps_receiver_validation(self):
        source = self.read("pages/orders/cart.vue")

        self.assertIn("@/utils/customer", source)
        self.assertIn("computed(() => isCashCustomerRecord(cart.customer))", source)
        self.assertNotIn("name.includes('一件代发')", source)
        for field in ("form.contact", "form.contact_phone", "form.ship_to"):
            with self.subTest(field=field):
                self.assertIn(field, source)
        self.assertIn("if (isCashCustomer.value)", source)
