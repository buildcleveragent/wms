"""Canonical locking and retry helpers for inventory writes."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import TypeVar

from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction

from allapp.locations.models import Warehouse

logger = logging.getLogger(__name__)

MYSQL_RETRYABLE_ERROR_CODES = {1205, 1213}
DEFAULT_INVENTORY_WRITE_ATTEMPTS = 3

ResultT = TypeVar("ResultT")


class InventoryConcurrencyError(ValidationError):
    """A retryable inventory-write collision that exhausted server retries."""

    error_code = "inventory_busy"
    retryable = True
    retry_after_seconds = 1

    def __init__(self):
        super().__init__("库存正在被其他业务更新，请稍后重试。", code=self.error_code)


def mysql_error_code(exc: BaseException) -> int | None:
    """Return a MySQL error number from a wrapped database exception."""

    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for value in getattr(current, "args", ()):
            if isinstance(value, int):
                return value
        current = current.__cause__ or current.__context__
    return None


def is_retryable_inventory_error(exc: BaseException) -> bool:
    """Whether an exception is a retryable MySQL lock wait/deadlock error."""

    return isinstance(exc, OperationalError) and mysql_error_code(exc) in (
        MYSQL_RETRYABLE_ERROR_CODES
    )


def lock_warehouses_for_inventory_write(
    warehouse_ids: int | Iterable[int],
) -> list[Warehouse]:
    """Lock warehouse rows in ascending order as the first inventory-write lock."""

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("仓库库存写锁必须在 transaction.atomic() 中获取。")
    if isinstance(warehouse_ids, int):
        normalized_ids = [warehouse_ids]
    else:
        normalized_ids = sorted({int(value) for value in warehouse_ids})
    if not normalized_ids:
        raise ValueError("至少需要一个仓库 ID。")

    warehouses = list(
        Warehouse.objects.select_for_update()
        .filter(pk__in=normalized_ids)
        .order_by("id")
    )
    if len(warehouses) != len(normalized_ids):
        found = {warehouse.id for warehouse in warehouses}
        missing = [value for value in normalized_ids if value not in found]
        raise ValidationError({"warehouse": f"仓库不存在：{missing}"})
    return warehouses


def run_inventory_write_with_retry(
    operation: Callable[[], ResultT],
    *,
    operation_name: str,
    attempts: int = DEFAULT_INVENTORY_WRITE_ATTEMPTS,
) -> ResultT:
    """Run fresh atomic attempts and translate exhausted MySQL lock collisions."""

    if attempts < 1:
        raise ValueError("attempts must be at least one")
    if transaction.get_connection().in_atomic_block:
        # An outer transaction cannot be replaced with a fresh one after a
        # deadlock. Preserve compatibility for explicitly atomic callers and
        # Django TestCase by executing once; normal POS requests enter here
        # outside a transaction and retain the full retry policy.
        logger.info(
            "inventory.write.retry_disabled_nested_atomic operation=%s",
            operation_name,
        )
        return operation()

    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except OperationalError as exc:
            if not is_retryable_inventory_error(exc):
                raise
            logger.warning(
                "inventory.write.retry operation=%s attempt=%s max_attempts=%s mysql_error=%s",
                operation_name,
                attempt,
                attempts,
                mysql_error_code(exc),
            )
            if attempt == attempts:
                raise InventoryConcurrencyError() from exc
            time.sleep(0.02 * attempt)

    raise AssertionError("inventory retry loop exited unexpectedly")
