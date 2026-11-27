"""Keep close calculations and signatures on one committed InnoDB read view."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection, transaction

_close_reads = ContextVar("daily_close_reads", default=None)


@contextmanager
def close_transaction():
    mode = _close_reads.get()
    if mode is None:
        mode = "locked"
        if (
            not connection.in_atomic_block
            and connection.get_autocommit()
            and connection.vendor == "mysql"
        ):
            # Applies only to the next transaction. The scope locks precede its first
            # consistent SELECT, so competing closers see the preceding commit.
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            mode = "snapshot"
    token = _close_reads.set(mode)
    try:
        with transaction.atomic():
            yield
    finally:
        _close_reads.reset(token)


def lock_nested_close_inputs(owner_id, warehouse_id):
    """An already active transaction cannot be switched to REPEATABLE READ."""
    if _close_reads.get() != "locked":
        return
    from allapp.inventory.models import InventoryDetail, InventoryHoldBalance, InventoryTransaction

    # The owner/warehouse FK locks also fence inserts of new stock layers and postings.
    # Taking these locks additionally fences edits to existing rows and their hold history.
    for model in (InventoryDetail, InventoryTransaction, InventoryHoldBalance):
        qs = model._base_manager.select_for_update().order_by("pk")
        if owner_id is not None:
            qs = qs.filter(owner_id=owner_id)
        if warehouse_id is not None:
            qs = qs.filter(warehouse_id=warehouse_id)
        for _ in qs.values_list("pk", flat=True).iterator(chunk_size=2000):
            pass
