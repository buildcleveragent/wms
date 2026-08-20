from unittest.mock import Mock, patch

from django.db import OperationalError
from django.test import SimpleTestCase

from allapp.inventory.locking import (
    InventoryConcurrencyError,
    is_retryable_inventory_error,
    run_inventory_write_with_retry,
)


class InventoryWriteRetryTests(SimpleTestCase):
    def test_mysql_deadlock_and_lock_wait_timeout_are_retryable(self):
        self.assertTrue(is_retryable_inventory_error(OperationalError(1205, "wait")))
        self.assertTrue(is_retryable_inventory_error(OperationalError(1213, "deadlock")))
        self.assertFalse(is_retryable_inventory_error(OperationalError(2006, "gone")))

    @patch("allapp.inventory.locking.time.sleep")
    def test_retry_uses_fresh_attempts_and_eventually_returns(self, sleep):
        operation = Mock(
            side_effect=[
                OperationalError(1213, "deadlock"),
                OperationalError(1205, "wait"),
                "ok",
            ]
        )

        result = run_inventory_write_with_retry(
            operation,
            operation_name="test.retry",
        )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("allapp.inventory.locking.time.sleep")
    def test_exhausted_retry_raises_business_validation_error(self, sleep):
        operation = Mock(side_effect=OperationalError(1213, "deadlock"))

        with self.assertRaises(InventoryConcurrencyError) as caught:
            run_inventory_write_with_retry(
                operation,
                operation_name="test.exhausted",
            )

        self.assertEqual(operation.call_count, 3)
        self.assertEqual(caught.exception.error_code, "inventory_busy")
        self.assertTrue(caught.exception.retryable)

    @patch("allapp.inventory.locking.transaction.get_connection")
    def test_nested_atomic_caller_executes_once_without_unsafe_retry(self, connection):
        connection.return_value.in_atomic_block = True
        operation = Mock(return_value="ok")

        result = run_inventory_write_with_retry(
            operation,
            operation_name="test.nested",
        )

        self.assertEqual(result, "ok")
        operation.assert_called_once_with()
