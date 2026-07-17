from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from allapp.outbound.views import (
    AssistedOutboundOrderViewSet,
    IdempotencyConflict,
)


class _Lines(list):
    def filter(self, **kwargs):
        return self


class AssistedIdempotencyFingerprintTests(SimpleTestCase):
    def setUp(self):
        self.view = AssistedOutboundOrderViewSet()
        self.persisted_etd = timezone.make_aware(
            datetime(2026, 8, 1, 9, 30),
            timezone.get_current_timezone(),
        )
        self.order = SimpleNamespace(
            owner_id=11,
            customer_id=22,
            src_bill_no="SRC-1",
            delivery_method="COURIER",
            etd=self.persisted_etd,
            contact="张三",
            contact_phone="13800000000",
            ship_to="测试地址",
            memo="备注",
            assistance_reason="现场委托",
            lines=_Lines(
                [SimpleNamespace(product_id=33, base_qty=Decimal("2.000"))]
            ),
        )

    def _payload(self):
        return {
            "owner_id": 11,
            "customer_id": 22,
            "items": [{"product_id": 33, "qty": "2"}],
            "src_bill_no": "SRC-1",
            "delivery_method": "COURIER",
            # A naive value is interpreted by DRF in the current timezone.
            "etd": "2026-08-01T09:30:00",
            "contact": "张三",
            "contact_phone": "13800000000",
            "ship_to": "测试地址",
            "remark": "备注",
            "assistance_reason": "现场委托",
        }

    def test_naive_etd_matches_persisted_aware_value(self):
        self.assertEqual(
            self.view._request_fingerprint(self._payload()),
            self.view._persisted_fingerprint(self.order),
        )

    def test_business_payload_change_does_not_match(self):
        payload = self._payload()
        payload["items"][0]["qty"] = "3"
        self.assertNotEqual(
            self.view._request_fingerprint(payload),
            self.view._persisted_fingerprint(self.order),
        )

    def test_same_request_id_with_changed_business_payload_is_conflict(self):
        payload = self._payload()
        payload["request_id"] = str(uuid4())
        payload["ship_to"] = "另一个地址"
        self.order.assisted_by_id = 7
        self.order.warehouse_id = 8
        user = SimpleNamespace(id=7, warehouse_id=8)
        existing_qs = mock.Mock()
        existing_qs.first.return_value = self.order

        with mock.patch(
            "allapp.outbound.views.OutboundOrder.objects.filter",
            return_value=existing_qs,
        ):
            with self.assertRaises(IdempotencyConflict) as exc:
                self.view._idempotent_result(payload, user)

        self.assertEqual(exc.exception.status_code, 409)

    def test_create_response_exposes_both_replay_flags(self):
        response = self.view._response(
            SimpleNamespace(
                id=1,
                order_no="CK-1",
                submit_status="SUBMITTED",
                approval_status="WHS_APPROVED",
            ),
            SimpleNamespace(id=2, task_no="JH-1", status="RELEASED"),
            idempotent=True,
            http_status=200,
        )
        self.assertTrue(response.data["idempotent"])
        self.assertTrue(response.data["replayed"])
