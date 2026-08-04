from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class OwnerOrderEditClientContractTests(SimpleTestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_review_menu_filters_submitted_pending_and_requires_reason(self):
        index = self.text("wmsownersale/pages/approval/index.vue")
        detail = self.text("wmsownersale/pages/approval/approvedetail.vue")
        actions = self.text("wmsownersale/utils/useOrderReviewActions.js")
        request = self.text("wmsownersale/utils/request.js")
        self.assertIn("submit_status: 'SUBMITTED'", index)
        self.assertIn("order.submit_status || '') === 'SUBMITTED'", actions)
        self.assertIn('v-model="rejectReason"', detail)
        self.assertIn("ownerReject: (id, reason)", request)
        self.assertIn("data: { reason:", request)

    def test_edit_context_and_two_save_actions_are_wired(self):
        cart = self.text("wmsownersale/store/cart.js")
        page = self.text("wmsownersale/pages/orders/cart.vue")
        detail = self.text("wmsownersale/pages/orders/detail.vue")
        request = self.text("wmsownersale/utils/request.js")
        self.assertIn("beginEdit({ user_id, owner_id, context })", cart)
        self.assertIn("changeWarehouseForEdit", cart)
        self.assertIn("保存草稿", page)
        self.assertIn("保存并重新提交", page)
        self.assertIn("api.updateOutboundOrder", page)
        self.assertIn("expected_updated_at", page)
        self.assertIn("stale_order_edit", page)
        self.assertIn("setEditingUpdatedAt", cart)
        self.assertIn("api.submitOutboundOrder", page)
        self.assertIn("api.orderEditContext", detail)
        self.assertIn("method: 'PUT'", request)
