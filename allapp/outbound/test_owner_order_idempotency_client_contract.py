import re
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / "wmsownersale"


class OwnerOrderIdempotencyClientContractTests(SimpleTestCase):
    def read(self, relative_path):
        return (CLIENT_ROOT / relative_path).read_text(encoding="utf-8")

    def test_cart_has_submit_lock_and_reuses_store_key(self):
        source = self.read("pages/orders/cart.vue")

        self.assertIn("const submitting = ref(false)", source)
        self.assertIn("if (submitting.value) return", source)
        self.assertIn(":loading=\"submitting\"", source)
        self.assertIn("!submitting.value", source)
        self.assertIn("finally", source)
        self.assertIn("submitting.value = false", source)
        self.assertIn("cart.ensureIdempotencyKey()", source)
        self.assertIn("cart.resetOrder()", source)

    def test_request_wrapper_requires_and_sends_idempotency_header(self):
        source = self.read("utils/request.js")

        self.assertIn("createOutboundOrder: (payload, idempotencyKey)", source)
        self.assertIn("MISSING_IDEMPOTENCY_KEY", source)
        self.assertIn("'Idempotency-Key': key", source)

    def test_cart_key_lifecycle_is_bound_to_the_logical_order(self):
        source = self.read("store/cart.js")

        self.assertIn("idempotency_key: null", source)
        self.assertIn("this.idempotency_key = createIdempotencyKey()", source)
        self.assertIn("ensureIdempotencyKey()", source)
        self.assertIn("if (!this.idempotency_key)", source)
        self.assertIn("this.idempotency_key = null", source)

    def test_uuid_generator_is_cross_platform_and_uuid_v4_shaped(self):
        source = self.read("utils/idempotency.js")

        self.assertIn("globalThis", source)
        self.assertIn("getRandomValues", source)
        self.assertIn("Math.random", source)
        self.assertRegex(source, re.compile(r"bytes\[6\].*0x40"))
        self.assertRegex(source, re.compile(r"bytes\[8\].*0x80"))

    def test_conflict_does_not_silently_generate_a_new_key(self):
        source = self.read("pages/orders/cart.vue")
        submit_block = source[source.index("async function submitOrder") :]
        conflict_block = submit_block[
            submit_block.index("=== 409") : submit_block.index("const duplicateMsg")
        ]

        self.assertIn("请返回并重新开单", conflict_block)
        self.assertNotIn("createIdempotencyKey", conflict_block)
        self.assertNotIn("resetOrder", conflict_block)
