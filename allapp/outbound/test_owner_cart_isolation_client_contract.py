from pathlib import Path

from django.test import SimpleTestCase


CLIENT_ROOT = Path(__file__).resolve().parents[2] / "wmsownersale"


class OwnerCartIsolationClientContractTests(SimpleTestCase):
    def read(self, relative_path):
        return (CLIENT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_order_context_is_bound_to_user_and_owner(self):
        source = self.read("store/cart.js")

        self.assertIn("user_id: null", source)
        self.assertIn("beginOrder({ user_id, owner_id, warehouse })", source)
        self.assertIn("this.user_id = user_id || null", source)
        self.assertIn("hasContextForUser", source)
        self.assertIn("String(s.user_id) === String(userId || '')", source)
        self.assertIn("String(s.owner_id) === String(ownerId || '')", source)

    def test_customer_switch_clears_items_and_rotates_key_only_for_new_id(self):
        source = self.read("store/cart.js")
        block = source[source.index("\tsetCustomer(c){") : source.index("\t// addItem")]

        self.assertLess(block.index("normalizeSelectedCustomer"), block.index("if (!customer)"))
        self.assertLess(block.index("if (!customer)"), block.index("previousCustomerId"))
        self.assertIn("String(previousCustomerId) !== String(customer.id)", block)
        self.assertIn("this.items = []", block)
        self.assertIn("this.idempotency_key = createIdempotencyKey()", block)
        self.assertLess(block.index("this.items = []"), block.index("this.customer = customer"))
        self.assertLess(
            block.index("this.idempotency_key = createIdempotencyKey()"),
            block.index("this.customer = customer"),
        )

    def test_full_reset_and_compatibility_clear_remove_all_context(self):
        source = self.read("store/cart.js")
        reset_block = source[source.index("\tresetOrder(){") : source.index("\tensureIdempotencyKey")]

        for assignment in (
            "this.user_id = null",
            "this.owner_id = null",
            "this.warehouse_id = null",
            "this.warehouse_name = ''",
            "this.idempotency_key = null",
            "this.customer = null",
            "this.items = []",
        ):
            with self.subTest(assignment=assignment):
                self.assertIn(assignment, reset_block)
        self.assertIn("clear(){ this.resetOrder() }", source)

    def test_logout_and_every_successful_login_reset_the_cart(self):
        source = self.read("store/auth.js")
        login_block = source[source.index("async login") : source.index("logout()")]
        logout_block = source[source.index("logout()") :]
        clear_block = source[source.index("clearLocalSession()") : source.index("async logout()")]

        self.assertIn("import { useCart } from '@/store/cart'", source)
        self.assertIn("const profile = await api.authProfile()", login_block)
        self.assertIn("useCart().resetOrder()", login_block)
        self.assertLess(
            login_block.index("const profile = await api.authProfile()"),
            login_block.index("useCart().resetOrder()"),
        )
        self.assertLess(
            login_block.index("useCart().resetOrder()"),
            login_block.index("return this.user"),
        )
        self.assertIn("await api.logout()", logout_block)
        self.assertIn("this.clearLocalSession()", logout_block)
        self.assertIn("useCart().resetOrder()", clear_block)
        self.assertLess(logout_block.index("await api.logout()"), logout_block.index("this.clearLocalSession()"))

    def test_all_registered_order_pages_fail_closed_on_user_or_owner_mismatch(self):
        for relative_path in (
            "pages/customers/select.vue",
            "pages/products/search.vue",
            "pages/orders/cart.vue",
        ):
            with self.subTest(page=relative_path):
                source = self.read(relative_path)
                guard = "cart.hasContextForUser(auth.user?.id, auth.user?.owner_id)"
                self.assertIn(guard, source)
                guard_block = source[source.index(guard) : source.index(guard) + 240]
                self.assertIn("cart.resetOrder()", guard_block)
                self.assertIn("/pages/warehouses/select", guard_block)

    def test_warehouse_selection_starts_context_for_authenticated_user(self):
        source = self.read("pages/warehouses/select.vue")
        begin_block = source[source.index("cart.beginOrder") : source.index("uni.redirectTo")]

        self.assertIn("user_id: auth.user?.id", begin_block)
        self.assertIn("owner_id: auth.user?.owner_id", begin_block)
        self.assertIn("!auth.user?.id || !auth.user?.owner_id", source)
