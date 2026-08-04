from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from allapp.salesapp.models import (
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniRefund,
)


def _queryset_returning_ids(ids):
    queryset = MagicMock()
    queryset.order_by.return_value.values_list.return_value.__getitem__.return_value = (
        list(ids)
    )
    return queryset


class ReconcileSaleMiniPaymentsCommandTests(SimpleTestCase):
    def test_command_reconciles_due_orders_payments_and_refunds(self):
        mappings = [SimpleNamespace(id=11), SimpleNamespace(id=12)]
        payments = [SimpleNamespace(id=21), SimpleNamespace(id=22)]
        refunds = [
            SimpleNamespace(id=31, next_retry_at=None),
            SimpleNamespace(id=32, next_retry_at=None),
        ]
        reconciled_refunds = [
            SimpleNamespace(
                status=SaleMiniRefund.Status.SUCCESS,
                requires_manual_action=False,
            ),
            SimpleNamespace(
                status=SaleMiniRefund.Status.PROCESSING,
                requires_manual_action=True,
            ),
        ]
        stdout = StringIO()

        with (
            patch.object(
                SaleMiniOrderMapping.objects,
                "filter",
                return_value=_queryset_returning_ids([11, 12]),
            ),
            patch.object(
                SaleMiniOrderMapping.objects,
                "get",
                side_effect=mappings,
            ),
            patch.object(
                SaleMiniPayment.objects,
                "filter",
                return_value=_queryset_returning_ids([21, 22]),
            ),
            patch.object(SaleMiniPayment.objects, "get", side_effect=payments),
            patch.object(
                SaleMiniRefund.objects,
                "filter",
                return_value=_queryset_returning_ids([31, 32]),
            ),
            patch.object(SaleMiniRefund.objects, "get", side_effect=refunds),
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.safely_cancel_unpaid_mapping",
                side_effect=[
                    {"result": "cancelled"},
                    {"result": "late_payment_refund_queued"},
                ],
            ) as cancel_mapping,
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.query_and_apply_payment",
                side_effect=[
                    {"trade_state": "SUCCESS"},
                    {"trade_state": "USERPAYING"},
                ],
            ) as query_payment,
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.reconcile_refund",
                side_effect=reconciled_refunds,
            ) as reconcile_refund,
        ):
            call_command("reconcile_sale_mini_payments", "--limit", "10", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("'cancelled': 1", output)
        self.assertIn("'paid': 1", output)
        self.assertIn("'waiting': 1", output)
        self.assertIn("'success': 1", output)
        self.assertIn("'manual': 1", output)
        self.assertEqual(cancel_mapping.call_count, 2)
        self.assertEqual(query_payment.call_count, 2)
        self.assertEqual(reconcile_refund.call_count, 2)

    def test_command_continues_other_work_then_raises_when_one_record_fails(self):
        mapping = SimpleNamespace(id=41)
        payment = SimpleNamespace(id=42)
        refund = SimpleNamespace(id=43, next_retry_at=None)
        stdout = StringIO()
        stderr = StringIO()

        with (
            patch.object(
                SaleMiniOrderMapping.objects,
                "filter",
                return_value=_queryset_returning_ids([41]),
            ),
            patch.object(SaleMiniOrderMapping.objects, "get", return_value=mapping),
            patch.object(
                SaleMiniPayment.objects,
                "filter",
                return_value=_queryset_returning_ids([42]),
            ),
            patch.object(SaleMiniPayment.objects, "get", return_value=payment),
            patch.object(
                SaleMiniRefund.objects,
                "filter",
                return_value=_queryset_returning_ids([43]),
            ),
            patch.object(SaleMiniRefund.objects, "get", return_value=refund),
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.safely_cancel_unpaid_mapping",
                side_effect=RuntimeError("gateway unavailable"),
            ),
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.query_and_apply_payment",
                return_value={"trade_state": "SUCCESS"},
            ) as query_payment,
            patch(
                "allapp.salesapp.management.commands."
                "reconcile_sale_mini_payments.reconcile_refund",
                return_value=SimpleNamespace(
                    status=SaleMiniRefund.Status.SUCCESS,
                    requires_manual_action=False,
                ),
            ) as reconcile_refund,
            self.assertRaises(CommandError),
        ):
            call_command(
                "reconcile_sale_mini_payments",
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn(
            "mapping 41 failed: RuntimeError: gateway unavailable", stderr.getvalue()
        )
        self.assertIn("'failed': 1", stdout.getvalue())
        query_payment.assert_called_once_with(payment)
        reconcile_refund.assert_called_once_with(refund)
